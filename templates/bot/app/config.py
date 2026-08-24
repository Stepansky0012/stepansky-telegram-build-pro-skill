"""Typed config, validated at startup. Fail fast and loudly, never at first use."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

BOT_API_PINNED = "10.2"          # keep in sync with .tgstack.json


class ConfigError(RuntimeError):
    pass


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise ConfigError(f"{name} is required but not set")
    return v


def _opt(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _opt(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


@dataclass(frozen=True)
class Config:
    env: str
    version: str
    bot_token: str = field(repr=False)          # never in a repr, never in a log
    redis_url: str
    database_url: str = field(repr=False)
    webhook_url: str
    webhook_secret: str = field(repr=False)
    webapp_url: str
    session_secret: str = field(repr=False)
    sticker_owner_user_id: int
    admin_ids: tuple[int, ...]
    log_message_text: bool
    session_ttl_sec: int

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url)

    def redacted(self) -> dict:
        """Safe to log at startup — proves what is configured without leaking it."""
        return {
            "env": self.env, "version": self.version,
            "bot_api_pinned": BOT_API_PINNED,
            "delivery": "webhook" if self.use_webhook else "polling",
            "webapp_url": self.webapp_url or None,
            "redis": bool(self.redis_url), "db": bool(self.database_url),
            "admins": len(self.admin_ids),
            "log_message_text": self.log_message_text,
        }


def load() -> Config:
    cfg = Config(
        env=_opt("ENV", "dev"),
        version=_opt("APP_VERSION", "0.0.0-dev"),
        bot_token=_req("BOT_TOKEN"),
        redis_url=_opt("REDIS_URL", "redis://localhost:6379/0"),
        database_url=_opt("DATABASE_URL"),
        webhook_url=_opt("WEBHOOK_URL"),
        webhook_secret=_opt("WEBHOOK_SECRET"),
        webapp_url=_opt("WEBAPP_URL"),
        session_secret=_opt("SESSION_SECRET"),
        sticker_owner_user_id=_int("STICKER_OWNER_USER_ID", 0),
        admin_ids=tuple(int(x) for x in _opt("ADMIN_IDS").replace(" ", "").split(",") if x),
        log_message_text=_opt("LOG_MESSAGE_TEXT", "0") == "1",
        session_ttl_sec=_int("SESSION_TTL_SEC", 3600),
    )
    # cross-field rules that are cheap to check and expensive to discover in prod
    if cfg.use_webhook and not cfg.webhook_secret:
        raise ConfigError("WEBHOOK_SECRET is required with WEBHOOK_URL — "
                          "without it your endpoint accepts forged updates")
    if cfg.webapp_url and not cfg.session_secret:
        raise ConfigError("SESSION_SECRET is required with WEBAPP_URL")
    if cfg.env == "prod":
        if not cfg.database_url:
            raise ConfigError("DATABASE_URL is required in prod")
        if cfg.log_message_text:
            raise ConfigError("LOG_MESSAGE_TEXT=1 is refused in prod — "
                              "message text is PII; enable it per handler instead")
    return cfg
