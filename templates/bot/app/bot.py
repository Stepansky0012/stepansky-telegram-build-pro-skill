"""Composition root: config -> observability -> gateway -> middlewares -> routers.

Nothing else in the project constructs a Bot, a Dispatcher or a gateway.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from . import config as cfgmod
from . import middlewares
from .handlers import start
from .observability import setup as setup_logging
from .tg.gateway import TgGateway
from .tg.limiter import Limiter

log = logging.getLogger("bot")


async def build(cfg: cfgmod.Config) -> tuple[Bot, Dispatcher, TgGateway]:
    # HTML is the default because MarkdownV2 is banned by Law 1 — but every
    # message should still be built with tg_text.H rather than by hand.
    bot = Bot(cfg.bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    gw = TgGateway(bot, Limiter())

    redis = None
    storage = MemoryStorage()
    if cfg.redis_url:
        try:
            from redis.asyncio import Redis
            from aiogram.fsm.storage.redis import RedisStorage
            redis = Redis.from_url(cfg.redis_url, decode_responses=True)
            await redis.ping()
            storage = RedisStorage(redis)
        except Exception as e:                                   # noqa: BLE001
            if cfg.env == "prod":
                raise RuntimeError(
                    "Redis is required in prod: MemoryStorage drops every "
                    "in-flight dialog on deploy") from e
            log.warning("startup", extra={"event": "startup",
                                          "detail": f"redis unavailable ({e}); "
                                                    f"MemoryStorage — dev only"})

    dp = Dispatcher(storage=storage)
    dp["gw"] = gw
    dp["cfg"] = cfg
    middlewares.install(dp, gw=gw, redis=redis, users=None)

    # High-priority router first: /start, /help, /settings must work from ANY
    # FSM state, and they clear it. Otherwise a user stuck mid-wizard is stuck.
    dp.include_router(start.router)
    # dp.include_router(nav.routers.router)   # generated from navigation.yaml
    return bot, dp, gw


async def run_polling(bot: Bot, dp: Dispatcher, cfg: cfgmod.Config) -> None:
    # Single instance only. Two pollers on one token produce 409 and both break.
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("startup", extra={"event": "startup", "detail": "polling"})
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def run_webhook(bot: Bot, dp: Dispatcher, cfg: cfgmod.Config) -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    await bot.set_webhook(
        cfg.webhook_url,
        secret_token=cfg.webhook_secret,          # verified per request below
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,               # set True when nav changes shape
    )
    info = await bot.get_webhook_info()
    if info.url != cfg.webhook_url:
        raise RuntimeError(f"webhook mismatch: registered {info.url!r}, "
                           f"configured {cfg.webhook_url!r}")

    app = web.Application()

    async def health(_):
        return web.json_response({"ok": True})

    async def ready(_):
        wh = await bot.get_webhook_info()
        ok = wh.url == cfg.webhook_url
        return web.json_response({"ok": ok, "webhook": wh.url}, status=200 if ok else 503)

    app.router.add_get("/health", health)
    app.router.add_get("/ready", ready)
    SimpleRequestHandler(dispatcher=dp, bot=bot,
                         secret_token=cfg.webhook_secret).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    log.info("startup", extra={"event": "startup", "detail": "webhook"})
    await web._run_app(app, host="0.0.0.0", port=8080)           # noqa: SLF001


async def main() -> int:
    try:
        cfg = cfgmod.load()
    except cfgmod.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)             # fail fast, loudly
        return 2
    setup_logging("bot", cfg.version, cfg.env)
    log.info("startup", extra={"event": "startup", **{
        k: v for k, v in cfg.redacted().items() if k in ("env", "detail")}})
    log.info("startup", extra={"event": "startup", "detail": str(cfg.redacted())})

    bot, dp, _gw = await build(cfg)
    try:
        if cfg.use_webhook:
            await run_webhook(bot, dp, cfg)
        else:
            await run_polling(bot, dp, cfg)
    finally:
        await bot.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
