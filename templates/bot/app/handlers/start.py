"""Reference handler. Shows every invariant in one file, and nothing else.

Handlers are thin: parse intent -> call a service -> respond. No business logic,
no SQL, no HTTP, no keyboard literals, no hand-written markup.
"""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..tg.presence import Presence, ProcState
from ..tg.text import H, E

router = Router(name="start")


# --------------------------------------------------------------------------- #
# Law 10: /start, /help, /settings work from ANY state and clear it.
# --------------------------------------------------------------------------- #

@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext, gw, command=None, **kw):
    await state.clear()
    payload = getattr(command, "args", None)          # deep link: ?start=<payload>
    text = H.b("Готов к работе.") + H("\n")
    if payload:
        # Log the payload as the acquisition channel; validate before trusting it.
        text += H("Вы пришли по ссылке: ") + H.code(payload[:64])
    await gw.send_message(msg.chat.id, text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(msg: Message, state: FSMContext, gw, **kw):
    await state.clear()
    # Generated from navigation.yaml — never hand-maintained prose.
    # from ..nav.help import render_help
    rows = [("/start", "начать заново"), ("/help", "этот список")]
    body = H.b("Что умеет бот") + H("\n")
    for cmd, what in rows:
        body += H.code(cmd) + H(" — ") + H(what) + H("\n")
    await gw.send_message(msg.chat.id, body, parse_mode="HTML")


@router.message(Command("settings"))
async def cmd_settings(msg: Message, state: FSMContext, gw, **kw):
    await state.clear()
    await gw.send_message(msg.chat.id, H("Настройки пока пусты."), parse_mode="HTML")


# --------------------------------------------------------------------------- #
# Presence Protocol: long work is visible, and always reaches a terminal state.
# --------------------------------------------------------------------------- #

@router.message(F.text & ~F.text.startswith("/"))
async def on_text(msg: Message, gw, trace_id: str = "-", **kw):
    async with Presence(gw, msg.chat.id, msg.message_id, trace_id=trace_id) as p:
        await p.set(ProcState.STUDYING)
        steps = 5
        await p.set(ProcState.WORKING, progress=(0, steps))
        for i in range(1, steps + 1):
            await asyncio.sleep(0.4)                  # a service call goes here
            await p.set(ProcState.WORKING, progress=(i, steps))

        # The status message becomes the answer: no dead "Работаю..." left behind.
        answer = (E().bold("Готово").text(" — обработано ")
                  .code(str(len(msg.text or ""))).text(" символов").build())
        await p.replace_with_result(answer[0], entities=answer[1])


# --------------------------------------------------------------------------- #
# Law 14: every callback is answered, on every path.
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("v1:nav:"))
async def on_nav(cb: CallbackQuery, gw, **kw):
    try:
        _v, _domain, action, _arg = cb.data.split(":", 3) + [""] * 0
        if action == "home":
            await gw.edit_message_text(cb.message.chat.id, cb.message.message_id,
                                       H.b("Главное меню"), parse_mode="HTML")
    finally:
        await gw.answer_callback_query(cb.id)
