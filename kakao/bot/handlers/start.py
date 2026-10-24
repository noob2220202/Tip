"""/start, /help: 사용 방법을 자세히 안내하고 이모티콘샵 링크를 보낸다."""
from __future__ import annotations

import aiosqlite
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.handlers.common import NO_PREVIEW, shop_keyboard
from core import users
from core.style import esc
from core.texts import Texts

router = Router(name="kakao_start")


@router.message(CommandStart())
async def start(message: Message, db: aiosqlite.Connection, texts: Texts) -> None:
    u = message.from_user
    await users.register(db, u.id, u.username)
    me = await message.bot.me()
    await message.answer(
        texts.get("start", name=esc(u.first_name), bot=me.username),
        link_preview_options=NO_PREVIEW,
        reply_markup=shop_keyboard(texts),
    )


@router.message(Command("help"))
async def help_(message: Message, texts: Texts) -> None:
    me = await message.bot.me()
    await message.answer(
        texts.get("help", bot=me.username), link_preview_options=NO_PREVIEW, reply_markup=shop_keyboard(texts)
    )
