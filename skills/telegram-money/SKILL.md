---
name: telegram-money
description: "Use when a Telegram bot or Mini App takes payments or monetizes — Telegram Stars, XTR, sendInvoice, createInvoiceLink, pre_checkout_query, successful_payment, subscriptions, refunds, gifts, TON, card providers, paywalls, usage limits, free tiers, affiliate programs — or when payments double-charge, fulfilment happens without payment, a refund is requested, or you must choose between Stars and a card provider."
---

# Money — Stars, subscriptions, and the economics you must design around

Two decisions dominate everything else: **which rail** and **what happens on the seam between payment and fulfilment.** Get those right and the rest is plumbing.

## Choosing the rail

| Rail | Use for | Effective cost | Notes |
|---|---|---|---|
**Telegram Stars (`XTR`)** | **digital goods and services** — subscriptions, credits, unlocks, in-app items | Telegram takes no cut, but the purchase chain does: **~32% when the user buys Stars on iOS/Android, ~3–4% on desktop** (store fee + Fragment conversion spread) | No approval, no provider, no KYC. Best UX by far: one tap, no card form. |
Card provider (Telegram Payments) | **physical goods**, real-world services, invoices | provider's rate (~2–3%) | Requires a provider token from BotFather; a merchant relationship; regional availability varies |
TON / crypto | crypto-native audiences, cross-border | ~1% + on-chain | wallet friction; volatility is your problem, not the user's |
External checkout link | complex carts, B2B, VAT/MoR | provider's rate | leaves Telegram — expect a large conversion drop; use a Mini App instead where possible |

**The rule that is not negotiable:** digital goods consumed inside the app must go through Stars. Apple's and Google's store rules apply to Telegram as a host app, and routing digital purchases around them is what gets apps pulled. Physical goods are the opposite: use a card provider, not Stars.

**Design around the 32%.** Same product, ~28 points of margin difference by device. Practical consequences: quote prices in Stars, not in fiat, so the fee is invisible to the user; nudge large purchases toward desktop where the difference is honest to mention ("выгоднее с компьютера"); size bundles so the desktop price point is the anchor; never build a business whose unit economics only work at desktop rates.

## The payment flow, and where it breaks

```
createInvoiceLink / sendInvoice   -> user pays
        ↓
pre_checkout_query   ── answer within 10 s, ok=True/False  ← LAST chance to refuse
        ↓
successful_payment   ── may arrive more than once
        ↓
persist charge (idempotent)  ->  fulfil  ->  confirm in chat
```

Two laws:

**Law A — answer `pre_checkout_query` within 10 seconds, always.** Unanswered means the payment fails silently, with no explanation to the user. This is where you re-validate: is the item still available, is the price still right, is the user still eligible. Refuse with a *human sentence* — `error_message` is shown to the user, so "Товар закончился, попробуйте другой размер" not "validation_failed".

**Law B — persist before you fulfil, keyed on `telegram_payment_charge_id`.**

```python
async def on_successful_payment(sp, user_id):
    async with db.tx():
        row = await payments.insert_if_absent(
            charge_id=sp.telegram_payment_charge_id,     # UNIQUE constraint
            user_id=user_id, amount=sp.total_amount,
            currency=sp.currency, payload=sp.invoice_payload)
        if row is None:                  # duplicate delivery — already handled
            log.info("payment.duplicate", extra={"charge_id": sp.telegram_payment_charge_id})
            return
        await fulfil(user_id, sp.invoice_payload)        # same transaction
    await confirm_in_chat(user_id, row)
```

The unique constraint lives in **Postgres**, not Redis — payment idempotency must survive a cache flush. Fulfilment in the same transaction as the charge row, or a crash between them leaves money without goods.

`invoice_payload` is your order key and it is opaque to Telegram. Put a real order id in it, not a description. Validate it belongs to the paying user before fulfilling — never trust it as authorization on its own.

## Subscriptions

```python
link = await gw.raw("createInvoiceLink", api_version="9.0",
                    title="Pro", description="...", payload=f"sub:{plan_id}",
                    currency="XTR", prices=[{"label": "Pro", "amount": 500}],
                    subscription_period=2592000)          # 30 days
```

- `subscription_period` is in seconds and Telegram supports a narrow set of values (30 days is the one to rely on) — **verify the current allowed values at `bots/api#createinvoicelink`** before promising a billing cadence to a business.
- Renewals arrive as further `successful_payment` updates and as `BotSubscriptionUpdated` (10.1+). Handle **both**; the update tells you about state changes you did not cause, such as a user cancelling.
- `editUserStarSubscription` cancels or reinstates from your side. Cancelling must be self-service in the bot — a subscription you cannot cancel in two taps generates refund requests and chargebacks of goodwill.
- Grace period: decide it before launch (typically 1–3 days of continued access after a failed renewal) and log every entry and exit from grace. Silent revocation mid-session is the worst possible moment.
- Store the subscription state machine explicitly: `trialing → active → grace → cancelled → expired`. Deriving access from "did the last payment succeed" breaks on every edge.

## Refunds

`refundStarPayment(user_id, telegram_payment_charge_id)`.

- Write the policy **before** launch and put it in `/help`: what is refundable, within what window, and what happens to already-consumed credits. After launch you are negotiating case by case.
- Refunding consumed digital goods is a product decision, not a technical one. The technical requirement is that fulfilment is reversible and *recorded* as reversed — a refund with no ledger entry is an accounting hole.
- Every refund is an audit event: actor, reason, charge id, amount, before/after entitlement.
- Track `refund_rate`. Above a few percent it is a product problem — unclear pricing, a broken flow, or a paywall that fires before value is demonstrated.

## Monetization patterns that work on this platform

| Pattern | Fits | Design note |
|---|---|---|
Credits / consumables | AI generation, per-request work | show the balance **in the message that consumes it**, not only in a menu |
Subscription | ongoing access, higher limits | needs a visible, working self-service cancel |
Freemium with a usage limit | anything metered | the limit must be *legible* before it is hit — "осталось 3 из 10" on every use |
Unlock / one-off | a single feature or artifact | best conversion when the paywall appears **after** the value is visible |
Affiliate program | growth | you set the revenue share and the attribution window; pairs with `startapp` payloads as the referral channel |
Gifts | social products | `sendGift`, unique gifts, `convertGiftToStars` — a retention mechanic, not a revenue line |
Tips | community, content | zero-friction on Stars; never the primary rail |

Paywall placement is the highest-leverage decision in the whole skill: **let the user reach the result, then charge to keep or extend it.** A paywall before the first outcome converts a fraction of one placed after it.

`getMyStarBalance` belongs on an internal dashboard, checked on a schedule. Withdrawal goes through Fragment and takes time — do not discover this on the day payroll needs it.

## Trust surface

Users decide whether to pay in about two seconds, on the strength of things that cost you nothing:

- Price, what is included, and the renewal date **in the invoice description** — not one tap away.
- The exact wording of what happens after payment, and how to get a refund.
- A confirmation message in the chat with an order number in `code()` so it is tappable-to-copy.
- `/help` containing pricing, refund policy and support contact. A paid bot without these reads as a scam regardless of quality.
- Never a countdown timer or a fake scarcity claim. On a platform where the payment rail is one tap, manufactured urgency reads as fraud and costs more than it earns.

## Instrumentation

| Event | Fields |
|---|---|
`payment.invoice_created` | `payload`, `amount`, `currency`, `rail` |
`payment.precheckout` | `ok`, `reason`, `latency_ms` |
`payment.paid` | `charge_id`, `amount`, `currency`, `is_recurring` |
`payment.duplicate` | `charge_id` |
`payment.fulfilled` | `charge_id`, `entitlement` |
`payment.refunded` | `charge_id`, `actor`, `reason` |
`subscription.state` | `from`, `to`, `plan` |

The alert that matters: **`payment.paid` count ≠ `payment.fulfilled` count** over any hour. That is money without goods, or goods without money, and it is the only payment alert you cannot afford to miss.

Retention for payment events is separate from application logs and outlives them — as long as your jurisdiction requires. Never redact `charge_id`.

## Common mistakes

| Mistake | Consequence |
|---|---|
Fulfil before persisting the charge | crash → paid, nothing delivered, no record |
Idempotency in Redis only | cache flush → double fulfilment |
`pre_checkout_query` answered late or not at all | silent payment failures nobody can debug |
`error_message` written for developers | user sees `validation_failed` |
Trusting `invoice_payload` as authorization | one user pays, another gets the goods |
Access derived from the last payment | breaks on grace, refunds, plan changes |
No self-service cancel | refund requests and reputational damage |
Digital goods on a card rail | store-policy exposure |
Pricing in fiat, charging in Stars | the ~32% mobile fee eats the margin invisibly |
Paywall before the first result | conversion collapses |
Refund with no ledger entry | accounting hole |

## Red flags

- A `successful_payment` handler with no unique constraint behind it.
- A `pre_checkout_query` handler that can take longer than 10 s (any network call in it must have a hard timeout).
- Any entitlement check that reads a payments table instead of an entitlement state.
- A price literal in code rather than in a plan definition.
- A subscription feature shipped without the cancel flow.
