# The gateway — reference implementation

One object, one place where the Bot API is touched. Everything below is deliberately boring; the value is that it exists exactly once.

## Limiter

```python
# app/tg/limiter.py
import asyncio, time
from dataclasses import dataclass


@dataclass
class Bucket:
    capacity: float
    refill_per_sec: float
    tokens: float = 0.0
    updated: float = 0.0
    penalty_until: float = 0.0        # set by a 429 for this scope

    def _refill(self, now: float) -> None:
        if self.updated == 0.0:
            self.tokens, self.updated = self.capacity, now
            return
        self.tokens = min(self.capacity,
                          self.tokens + (now - self.updated) * self.refill_per_sec)
        self.updated = now

    def wait_for(self, now: float) -> float:
        """Seconds to wait before one token is available."""
        if now < self.penalty_until:
            return self.penalty_until - now
        self._refill(now)
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.refill_per_sec

    def take(self, now: float) -> None:
        self._refill(now)
        self.tokens -= 1.0


class Limiter:
    """Budgets, not guarantees. The only authority is retry_after from a 429."""

    GLOBAL = Bucket(capacity=30, refill_per_sec=30)

    def __init__(self) -> None:
        self._chat: dict[int, Bucket] = {}
        self._group: dict[int, Bucket] = {}
        self._lock = asyncio.Lock()

    def _for(self, chat_id: int, is_group: bool) -> list[Bucket]:
        b = [self.GLOBAL,
             self._chat.setdefault(chat_id, Bucket(capacity=1, refill_per_sec=1))]
        if is_group:
            b.append(self._group.setdefault(
                chat_id, Bucket(capacity=20, refill_per_sec=20 / 60)))
        return b

    async def acquire(self, chat_id: int, is_group: bool = False) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                buckets = self._for(chat_id, is_group)
                wait = max(b.wait_for(now) for b in buckets)
                if wait <= 0:
                    for b in buckets:
                        b.take(now)
                    return
            await asyncio.sleep(min(wait, 5.0))

    def penalize(self, chat_id: int, retry_after: float, scope: str = "chat") -> None:
        """Called from the 429 handler. Honours Telegram's own number."""
        until = time.monotonic() + retry_after
        target = self.GLOBAL if scope == "global" else \
            self._chat.setdefault(chat_id, Bucket(1, 1))
        target.penalty_until = max(target.penalty_until, until)
```

Notes: a single lock keeps the buckets consistent and is not a bottleneck at Telegram's throughput. `capacity == refill_per_sec` for the global bucket means "burst of 30 then 30/s", which matches observed behaviour. The per-group bucket refills at 20/60 because the group limit is per minute, not per second.

## Error taxonomy

```python
# app/tg/errors.py
from enum import Enum
import re


class TgAction(str, Enum):
    IGNORE = "ignore"; RETRY = "retry"; DEGRADE = "degrade"
    INACTIVE = "inactive"; FIX = "fix"; ALERT = "alert"


_RULES: list[tuple[re.Pattern, TgAction]] = [
    (re.compile(r"message is not modified", re.I),        TgAction.IGNORE),
    (re.compile(r"query is too old|query id is invalid", re.I), TgAction.IGNORE),
    (re.compile(r"message to (edit|delete) not found", re.I), TgAction.DEGRADE),
    (re.compile(r"message can't be edited", re.I),        TgAction.DEGRADE),
    (re.compile(r"can't parse entities", re.I),           TgAction.FIX),
    (re.compile(r"BUTTON_DATA_INVALID", re.I),            TgAction.FIX),
    (re.compile(r"wrong (file identifier|remote file id)", re.I), TgAction.FIX),
    (re.compile(r"STICKER_\w+|STICKERSET_INVALID", re.I), TgAction.FIX),
    (re.compile(r"bot was blocked|user is deactivated|"
                r"bot was kicked|CHAT_WRITE_FORBIDDEN", re.I), TgAction.INACTIVE),
    (re.compile(r"chat not found|PEER_ID_INVALID", re.I), TgAction.INACTIVE),
    (re.compile(r"not enough rights", re.I),              TgAction.DEGRADE),
    (re.compile(r"terminated by other getUpdates", re.I), TgAction.FIX),
]


def classify(status: int, description: str) -> TgAction:
    if status == 429:
        return TgAction.RETRY
    if status in (500, 502, 503, 504):
        return TgAction.RETRY
    if status == 401:
        return TgAction.FIX
    for pat, action in _RULES:
        if pat.search(description or ""):
            return action
    return TgAction.ALERT          # unknown is never IGNORE
```

`ALERT` as the default is the whole point. A silent default is how a bot drifts out of sync with the API and nobody notices for a quarter.

## Gateway

```python
# app/tg/gateway.py
import asyncio, random, time
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

SOFT_OK = {TgAction.IGNORE, TgAction.DEGRADE}
MAX_ATTEMPTS = 3
STATUS_THROTTLE_SEC = 1.2


class TgGateway:
    def __init__(self, bot, limiter: Limiter, *, metrics, log):
        self.bot, self.lim, self.m, self.log = bot, limiter, metrics, log
        self._last_status_edit: dict[int, float] = {}

    # ---------------- core ----------------
    async def call(self, method: str, *, chat_id: int | None = None,
                   is_group: bool = False, soft: bool = False, **params):
        attempt = 0
        started_total = time.monotonic()
        while True:
            attempt += 1
            if chat_id is not None:
                await self.lim.acquire(chat_id, is_group)
            t0 = time.monotonic()
            try:
                result = await self._invoke(method, params)
                self._observe(method, "ok", t0, attempt)
                return result
            except TelegramAPIError as e:
                action = classify(e.status, e.message)
                self.m.inc("tg_api_error", code=str(e.status), action=action.value)
                self._observe(method, action.value, t0, attempt, err=e.message)

                if action is TgAction.RETRY:
                    delay = self._retry_delay(e, attempt)
                    if e.status == 429 and chat_id is not None:
                        self.lim.penalize(chat_id, delay)
                        self.m.inc("tg_rate_limited", scope="chat")
                    if attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(delay)
                        continue
                    action = TgAction.DEGRADE

                if soft and action in SOFT_OK:
                    return None
                if action is TgAction.IGNORE:
                    return None
                if action is TgAction.INACTIVE:
                    raise ChatUnreachable(chat_id) from e
                if action is TgAction.DEGRADE:
                    raise SendDegraded(method) from e
                raise    # FIX and ALERT surface: they are bugs, not conditions
            finally:
                self.m.observe("tg_api_total_latency", time.monotonic() - started_total,
                               method=method)

    @staticmethod
    def _retry_delay(e, attempt: int) -> float:
        ra = getattr(e, "retry_after", None)
        if ra:                                   # Telegram's own number wins, exactly
            return float(ra)
        return min(2 ** attempt, 8) * (0.5 + random.random())   # jitter

    def _observe(self, method, outcome, t0, attempt, err=None):
        self.m.inc("tg_api_calls", method=method, outcome=outcome)
        self.m.observe("tg_api_latency", time.monotonic() - t0, method=method)
        self.log.info("tg_api", extra={
            "trace_id": trace_id_var.get(), "method": method, "outcome": outcome,
            "attempt": attempt, "latency_ms": round((time.monotonic() - t0) * 1000),
            "error": err,
        })

    # ---------------- typed façade ----------------
    async def send_message(self, chat_id, text, **kw):
        return await self.call("sendMessage", chat_id=chat_id, text=text,
                               is_group=_is_group(chat_id), **kw)

    async def edit_message_text(self, chat_id, message_id, text, *, soft=False, **kw):
        return await self.call("editMessageText", chat_id=chat_id,
                               message_id=message_id, text=text, soft=soft, **kw)

    async def edit_status(self, chat_id, message_id, text, **kw):
        """Throttled, coalescing, always soft. For Presence only."""
        now = time.monotonic()
        if now - self._last_status_edit.get(chat_id, 0) < STATUS_THROTTLE_SEC:
            return None
        self._last_status_edit[chat_id] = now
        return await self.edit_message_text(chat_id, message_id, text, soft=True, **kw)

    async def set_message_reaction(self, chat_id, message_id, reaction, *, soft=True):
        return await self.call("setMessageReaction", chat_id=chat_id,
                               message_id=message_id, reaction=reaction, soft=soft)

    async def raw(self, method: str, *, api_version: str, **params):
        """Escape hatch for methods the library does not wrap yet.
        api_version is required so grep tells you what to revisit on upgrade."""
        return await self.call(method, **params)
```

Design decisions worth defending:

- **`soft=True` swallows only IGNORE/DEGRADE.** A cosmetic reaction failing must not fail the user's work — but a `FIX`-class bug in a cosmetic call is still a bug and still raises.
- **`FIX` and `ALERT` always propagate.** They are the two classes you must never learn to live with.
- **429 penalizes the bucket, then retries.** Retrying without penalizing just hits the wall again.
- **`edit_status` is a separate method,** so throttling cannot be forgotten by a caller and cannot be applied by accident to a real message.
- **`raw()` requires `api_version`** as a keyword. It is documentation you cannot skip.

## Broadcast

The only place where throughput matters, and the place where 403 handling is the difference between a working broadcast and none.

```python
async def broadcast(gw, repo, user_ids, render, *, concurrency=8):
    sem, sent, failed, inactive = asyncio.Semaphore(concurrency), 0, 0, 0

    async def one(uid: int):
        nonlocal sent, failed, inactive
        try:
            text, ents = render(uid)
            await gw.send_message(uid, text, entities=ents)
            sent += 1
        except ChatUnreachable:
            await repo.mark_inactive(uid)          # never retry these
            inactive += 1
        except SendDegraded:
            await repo.enqueue_retry(uid)          # try later, out of band
            failed += 1

    async with asyncio.TaskGroup() as tg:
        for uid in user_ids:
            await sem.acquire()
            tg.create_task(one(uid), name=f"bc:{uid}").add_done_callback(
                lambda _: sem.release())
    return {"sent": sent, "failed": failed, "inactive": inactive}
```

Rules: the limiter — not the semaphore — controls rate; the semaphore only bounds memory. `ChatUnreachable` is terminal and must mark the user inactive, or every future broadcast pays for the same dead chats. Report the three counters; a broadcast with no numbers is not auditable.
