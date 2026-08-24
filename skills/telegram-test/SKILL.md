---
name: telegram-test
description: "Use when writing or reviewing tests for a Telegram bot or Mini App — handler tests, mocked Bot API, FSM flows, callback routing, keyboard assertions, formatting and entity offsets, payment idempotency, initData validation, streaming block conversion — or before claiming a bot works, and when a bug reached production that a test should have caught."
---

# Testing a Telegram bot

You cannot test a Telegram bot by using it. The failure modes that matter are data-dependent (a name with a dot), timing-dependent (a 429 mid-broadcast), or delivery-dependent (a retried webhook) — none of which you will reproduce by tapping buttons.

**The unit under test is the set of outgoing API calls.** Given an incoming update and a state, the bot must make exactly these calls with exactly these payloads. Everything below follows from that.

## Layers, and what belongs in each

| Layer | Tool | Asserts |
|---|---|---|
Formatting | plain pytest | escaping, UTF-16 offsets, entity validity, safe splitting |
Contract | `gen_navigation.py --check` | reachability, depth ≤2, escapes, namespaces, orphan jobs |
Handlers | mocked Bot API (`aiogram-test-framework` / `aiogram_tests` / `grammy_tests`) | the outgoing calls, the FSM transition, the keyboard |
Services | plain pytest, no Telegram at all | business rules |
Gateway | fake transport | limiter, `retry_after` honoured, taxonomy, `soft` semantics |
Payments | pytest + real DB | idempotency under duplicate delivery |
Mini App | pytest (API) + Playwright (frontend) | initData validation, session, theme/safe-area, gesture zones |
Smoke | a real test bot in CI | webhook registered, `getMe`, one round trip |

The frontier: **anything that has ever broken in production gets a test at the lowest layer that could have caught it.**

## Handler tests

```python
async def test_take_request_sets_assignee_and_edits_in_place(bot: MockedBot, db):
    await db.seed(request=Request(id=81, status="new"))
    bot.add_result_for(EditMessageText, ok=True)

    await dispatch(bot, callback_query(data="v1:req:take:81",
                                      message_id=500, from_user=STAFF))

    calls = bot.calls
    assert method_names(calls) == ["setMessageReaction", "editMessageText",
                                   "answerCallbackQuery"]
    edit = call_of(calls, EditMessageText)
    assert edit.message_id == 500                      # edited in place, Law 9
    assert "В работе" in edit.text
    assert (await db.get(Request, 81)).assignee_id == STAFF.id
```

What every handler test asserts, without exception:

1. **The set and order of API calls.** Order encodes the UX: acknowledgement before work.
2. **`answerCallbackQuery` is present** — on the success path *and* on every error path (Law 14). Parametrize the failure.
3. **Edit vs send.** A test that accepts either has stopped testing the interaction model.
4. **The keyboard**, compared against the generated builder — never against a hand-written literal, or the test just re-encodes the bug.
5. **The FSM transition**, both the new state and the data written.

```python
@pytest.mark.parametrize("failure", [ServiceUnavailable, PermissionDenied, ValueError])
async def test_callback_always_answered(bot, failure):
    with patch("app.services.requests.take", side_effect=failure):
        await dispatch(bot, callback_query(data="v1:req:take:81"))
    assert call_of(bot.calls, AnswerCallbackQuery) is not None
```

## Formatting tests — property-based, because the bug is data-dependent

```python
DANGEROUS = ["ул. Ленина (д.5)", "a_b*c", "file[1].pdf", "-42%", "```", "🔎x🔎",
             "München", "R&D <ops>", "100$ = 90€"]

@pytest.mark.parametrize("s", DANGEROUS)
def test_html_roundtrip_never_breaks(s):
    out = str(H.b("Заявка ") + H.code(s))
    assert "<b>" in out and out.count("<code>") == 1
    assert "&" not in s or "&amp;" in out

@given(st.text())
def test_entity_offsets_are_utf16(s):
    text, ents = E().text("🔎").bold(s).build()
    if ents:
        assert ents[0]["offset"] == 2                  # the emoji is 2 u16 units
        assert ents[0]["length"] == u16len(s)

@given(st.text(min_size=1), st.integers(min_value=10, max_value=200))
def test_split_safe_preserves_entities(s, limit):
    text, ents = E().bold(s).build()
    for chunk, ce in split_safe(text, ents, limit=limit):
        for e in ce:
            assert 0 <= e["offset"] <= u16len(chunk)
            assert e["offset"] + e["length"] <= u16len(chunk)
```

Any string that has ever caused a production formatting failure joins `DANGEROUS` permanently. The list only grows.

## Gateway tests

```python
async def test_429_honours_retry_after_and_penalizes(gw, transport, clock):
    transport.queue(TelegramRetryAfter(retry_after=7), ok_response())
    await gw.send_message(1, "x")
    assert clock.slept == [7]                     # exactly Telegram's number
    assert transport.attempts == 2

async def test_soft_swallows_not_modified_but_not_parse_error(gw, transport):
    transport.queue(BadRequest("message is not modified"))
    assert await gw.edit_message_text(1, 2, "x", soft=True) is None

    transport.queue(BadRequest("can't parse entities"))
    with pytest.raises(BadRequest):               # FIX-class always surfaces
        await gw.edit_message_text(1, 2, "x", soft=True)

async def test_unknown_error_alerts(gw, transport, metrics):
    transport.queue(BadRequest("some brand new failure"))
    with pytest.raises(BadRequest):
        await gw.send_message(1, "x")
    assert metrics.counter("tg_api_error", action="alert") == 1
```

The third test is the one that keeps the stack honest: an unmapped error must **alert**, never quietly succeed.

## Idempotency and delivery

```python
async def test_duplicate_update_is_dropped(bot, redis):
    upd = message_update(update_id=999, text="/start")
    await dispatch(bot, upd)
    n = len(bot.calls)
    await dispatch(bot, upd)                      # Telegram retried
    assert len(bot.calls) == n

async def test_duplicate_payment_fulfils_once(db, bot):
    sp = successful_payment(charge_id="ch_1", amount=500, currency="XTR")
    await on_successful_payment(sp, USER)
    await on_successful_payment(sp, USER)
    assert await db.count(Entitlement, user_id=USER) == 1
```

## Streaming / rich

The partial-safe block converter gets the hardest test in the suite: every prefix of a known output must produce a valid prefix of the final blocks.

```python
@pytest.mark.parametrize("md", [MD_WITH_TABLE, MD_WITH_FENCE, MD_WITH_NESTED_LIST])
def test_partial_blocks_are_always_prefixes(md):
    final = md_to_rich_blocks(md, partial=False)
    for i in range(1, len(md) + 1):
        partial = md_to_rich_blocks(md[:i], partial=True)
        assert is_prefix_of(partial, final), f"broke at {i}"
        assert not has_open_block(partial)
```

Also: assert the throttle. A streaming test that does not assert the *number* of draft calls will pass while the bot 429s in production.

## Mini App

| Test | Asserts |
|---|---|
initData valid | 200 + session issued |
initData tampered (one byte in `user`) | 401 |
`hash` removed | 401 |
`auth_date` 48 h old | 401 |
`auth_date` 2 h old on a money route (`max_age=3600`) | 401 |
`user_id` in the request body ≠ session | ignored; session wins |
Every mutating route | leaves a chat-confirmation side effect |
Playwright: theme | no computed colour outside the token set |
Playwright: safe area | no content within `--edge-guard` of an edge for swipeable elements |
Playwright: `100vh` | absent from computed styles |

The tampered-initData test is the one that must exist even if you write no other test. It is the difference between an app and an impersonation vector.

## Fixtures worth building once

```python
def message_update(*, text, update_id=1, chat_id=1, user=USER, message_id=100): ...
def callback_query(*, data, message_id=100, from_user=USER, update_id=1): ...
def successful_payment(*, charge_id, amount, currency="XTR", payload="ord:1"): ...
def init_data(*, user_id, auth_date=None, token=TEST_TOKEN, tamper=None): ...
def method_names(calls) -> list[str]: ...
def call_of(calls, method_cls): ...
```

`init_data(tamper=...)` that signs correctly and then mutates one field is the fixture that makes the whole auth test class trivial to write — build it first.

## CI gates

```yaml
- run: python scripts/tg_preflight.py --project .          # all 30 invariants
- run: python scripts/gen_navigation.py navigation.yaml --check
- run: python scripts/validate_sticker_assets.py assets/ --kind custom_emoji
- run: pytest -q --cov=app --cov-fail-under=80
- run: pytest -q tests/smoke --telegram-live               # test bot, main branch only
```

Coverage floor is for `services/` and `handlers/`; generated code under `app/nav/` is excluded — it is tested by the generator's own checks, not by handler tests.

## Common mistakes

| Mistake | Why it hurts |
|---|---|
Asserting only "did not raise" | the bot can send nothing and pass |
Comparing keyboards to hand-written literals | the test encodes the same bug as the code |
Testing only the happy path | the interesting paths are 429, 403, duplicate delivery, timeout |
`sleep()` in tests | flaky and slow; inject a fake clock |
Live Telegram calls in unit tests | rate-limited, non-deterministic, unusable in PR CI |
No formatting property tests | the data-dependent bug ships |
Mocking your own service instead of the Bot API | tests the mock, not the handler |
Streaming tested without asserting call count | passes in CI, 429s in production |
