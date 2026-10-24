"""/start, /help: 사용 방법을 자세히 안내하고 메뉴 버튼을 보여준다."""
from __future__ import annotations

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.common import NO_PREVIEW, menu_keyboard
from core import users
from core.style import esc
from core.texts import Texts

router = Router(name="boost_start")


@router.message(CommandStart())
async def start(message: Message, db: aiosqlite.Connection, texts: Texts, state: FSMContext, ttl_min: int) -> None:
    await state.clear()
    u = message.from_user
    await users.register(db, u.id, u.username)
    await message.answer(texts.get("start", name=esc(u.first_name), ttl=ttl_min),
                         link_preview_options=NO_PREVIEW, reply_markup=menu_keyboard(texts))


@router.message(Command("help"))
async def help_(message: Message, texts: Texts, ttl_min: int) -> None:
    await message.answer(texts.get("help", ttl=ttl_min), link_preview_options=NO_PREVIEW,
                         reply_markup=menu_keyboard(texts))


@router.message(Command("menu"))
async def menu(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("menu_title"), reply_markup=menu_keyboard(texts))


@router.message(Command("cancel"))
async def cancel(message: Message, texts: Texts, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.get("flow_cancelled"), reply_markup=menu_keyboard(texts))


@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery, texts: Texts, state: FSMContext) -> None:
    await state.clear()
    await cb.message.answer(texts.get("menu_title"), reply_markup=menu_keyboard(texts))
    await cb.answer()
