"""/start, /menu, /cancel, 사용 방법."""
from __future__ import annotations

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from bot import texts as T
from core import users
from core.texts import Texts

router = Router(name="rank_start")
router.message.filter(F.chat.type == "private")
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


@router.message(CommandStart())
async def start(message: Message, db: aiosqlite.Connection, texts: Texts, state: FSMContext) -> None:
    await state.clear()
    await users.register(db, message.from_user.id, message.from_user.username)
    await message.answer(texts.get("start"), reply_markup=T.menu_kb())


@router.message(Command("menu"))
async def menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("📋 <b>메뉴</b>", reply_markup=T.menu_kb())


@router.message(Command("help"))
async def help_(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("help"), reply_markup=T.menu_kb(), link_preview_options=_NO_PREVIEW)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(T.CANCELLED, reply_markup=T.menu_kb())


@router.callback_query(F.data == "help")
async def help_cb(cb: CallbackQuery, texts: Texts) -> None:
    await cb.message.answer(texts.get("help"), reply_markup=T.menu_kb(), link_preview_options=_NO_PREVIEW)
    await cb.answer()

