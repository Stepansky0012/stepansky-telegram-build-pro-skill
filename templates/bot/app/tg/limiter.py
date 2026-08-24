"""Token-bucket rate limiter: per chat, per group, global.

These are budgets, not guarantees. The only authority is the `retry_after` that
Telegram hands you in a 429 — `penalize()` is how that authority gets applied.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

PER_CHAT_PER_SEC = 1.0
PER_GROUP_PER_MIN = 20.0
GLOBAL_PER_SEC = 30.0


@dataclass
class Bucket:
    capacity: float
    refill_per_sec: float
    tokens: float = 0.0
    updated: float = 0.0
    penalty_until: float = 0.0

    def _refill(self, now: float) -> None:
        if self.updated == 0.0:
            self.tokens, self.updated = self.capacity, now
            return
        self.tokens = min(self.capacity,
                          self.tokens + (now - self.updated) * self.refill_per_sec)
        self.updated = now

    def wait_for(self, now: float) -> float:
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
    def __init__(self, *, metrics=None) -> None:
        self._global = Bucket(GLOBAL_PER_SEC, GLOBAL_PER_SEC)
        self._chat: dict[int, Bucket] = {}
        self._group: dict[int, Bucket] = {}
        self._lock = asyncio.Lock()
        self._m = metrics

    def _chat_bucket(self, chat_id: int) -> Bucket:
        return self._chat.setdefault(chat_id, Bucket(1, PER_CHAT_PER_SEC))

    def _buckets(self, chat_id: int, is_group: bool) -> list[Bucket]:
        out = [self._global, self._chat_bucket(chat_id)]
        if is_group:
            out.append(self._group.setdefault(
                chat_id, Bucket(PER_GROUP_PER_MIN, PER_GROUP_PER_MIN / 60.0)))
        return out

    async def acquire(self, chat_id: int, is_group: bool = False) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                buckets = self._buckets(chat_id, is_group)
                wait = max(b.wait_for(now) for b in buckets)
                if wait <= 0:
                    for b in buckets:
                        b.take(now)
                    return
            if self._m:
                self._m.inc("tg_rate_limited", scope="local")
            await asyncio.sleep(min(wait, 5.0))

    def penalize(self, chat_id: int | None, retry_after: float,
                 scope: str = "chat") -> None:
        """Apply Telegram's own retry_after. Retrying without this just hits the
        wall again."""
        until = time.monotonic() + float(retry_after)
        target = self._global if scope == "global" or chat_id is None \
            else self._chat_bucket(chat_id)
        target.penalty_until = max(target.penalty_until, until)
        if self._m:
            self._m.inc("tg_rate_limited", scope=scope)

    def prune(self, max_buckets: int = 50_000) -> None:
        """Bounded memory for long-lived processes with many chats."""
        for store in (self._chat, self._group):
            if len(store) > max_buckets:
                now = time.monotonic()
                stale = [k for k, b in store.items()
                         if b.penalty_until < now and now - b.updated > 300]
                for k in stale[: len(store) - max_buckets // 2]:
                    store.pop(k, None)
