"""일반 사용자: /start, /help. 관리자에게는 관리 메뉴를 함께 보여준다."""
from __future__ import annotations

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import texts as T
from core import users
from core.config import BotConfig
from core.texts import Texts

router = Router(name="giveaway_start")
router.message.filter(F.chat.type == "private")


@router.message(CommandStart())
async def start(message: Message, db: aiosqlite.Connection, texts: Texts, cfg: BotConfig, state: FSMContext) -> None:
    await state.clear()
    await users.register(db, message.from_user.id, message.from_user.username)      # 이 등록이 곧 '이벤트 대상' 등록이다
    is_admin = message.from_user.id in cfg.admin_ids
    await message.answer(texts.get("start"), reply_markup=T.admin_menu_kb() if is_admin else None)


@router.message(Command("help"))
async def help_(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("help"))


@router.message(F.text)
async def fallback(message: Message, cfg: BotConfig) -> None:
    if message.from_user.id in cfg.admin_ids:
        await message.answer(T.UNKNOWN, reply_markup=T.admin_menu_kb())
