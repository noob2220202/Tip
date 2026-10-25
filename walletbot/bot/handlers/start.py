"""/start, 메뉴, 취소, 잔액·시세 조회, 상태 없이 온 텍스트."""
from __future__ import annotations

import asyncio

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from bot import store
from bot import texts as T
from bot.chain import TronError, TronReader
from bot.prices import PriceError, Prices
from core import users
from core.texts import Texts

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
PRIVATE = F.chat.type == "private"

router = Router(name="wallet_start")
router.message.filter(PRIVATE)
fallback_router = Router(name="wallet_fallback")
fallback_router.message.filter(PRIVATE)


def korean(words: str):
    """텔레그램은 /뒤에 영문·숫자만 명령어로 쳐주므로 한글 명령어는 일반 텍스트를 정규식으로 잡는다."""
    return F.text.regexp(rf"^\s*[/／]\s*(?:{words})(?:@\S+)?\s*$")


async def show(target: Message | CallbackQuery, text: str, kb) -> None:
    """버튼에서 눌렀으면 그 메시지를 고치고, 명령어로 불렀으면 새로 보낸다."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb, link_preview_options=NO_PREVIEW)
        except Exception:  # noqa: BLE001 - 내용이 같거나 너무 오래된 메시지면 새로 보낸다
            await target.message.answer(text, reply_markup=kb, link_preview_options=NO_PREVIEW)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, link_preview_options=NO_PREVIEW)


@router.message(CommandStart())
async def start(message: Message, db: aiosqlite.Connection, texts: Texts, state: FSMContext) -> None:
    await state.clear()
    await users.register(db, message.from_user.id, message.from_user.username)
    await message.answer(texts.get("start"), reply_markup=T.menu_kb())


@router.message(Command("menu"))
async def menu_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("📋 <b>메뉴</b>", reply_markup=T.menu_kb())


@router.message(Command("help"))
async def help_cmd(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("help"), reply_markup=T.menu_kb())


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(T.CANCELLED, reply_markup=T.menu_kb())


@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, "📋 <b>메뉴</b>", T.menu_kb())


@router.callback_query(F.data == "help")
async def help_cb(cb: CallbackQuery, texts: Texts) -> None:
    await show(cb, texts.get("help"), T.menu_kb())


# ---- 시세 ----

async def _price(prices: Prices) -> tuple[str, object]:
    try:
        return T.price_text(await prices.get()), T.refresh_kb("px")
    except PriceError:
        return T.PRICE_FAIL, T.refresh_kb("px")


@router.message(Command("price"))
@router.message(korean("시세"))
async def price_cmd(message: Message, prices: Prices) -> None:
    await show(message, *await _price(prices))


@router.callback_query(F.data == "px")
async def price_cb(cb: CallbackQuery, prices: Prices) -> None:
    await show(cb, *await _price(prices))


# ---- 잔액 ----

async def _balance(user_id: int, db: aiosqlite.Connection, chain: TronReader, prices: Prices) -> tuple[str, object]:
    wallets = await store.wallets_of(db, user_id)
    if not wallets:
        return T.NO_WALLET, T.back_kb()

    async def one(w):
        try:
            return await chain.balance(w["address"])
        except TronError:
            return None

    bals = await asyncio.gather(*(one(w) for w in wallets))
    try:
        quote = await prices.get()
    except PriceError:
        quote = None
    if all(b is None for b in bals):
        return T.CHAIN_FAIL, T.refresh_kb("bal")
    return T.balance_text(list(zip(wallets, bals)), quote), T.refresh_kb("bal")


@router.message(Command("balance"))
@router.message(korean("잔액"))
async def balance_cmd(message: Message, db: aiosqlite.Connection, chain: TronReader, prices: Prices) -> None:
    await show(message, *await _balance(message.from_user.id, db, chain, prices))


@router.callback_query(F.data == "bal")
async def balance_cb(cb: CallbackQuery, db: aiosqlite.Connection, chain: TronReader, prices: Prices) -> None:
    await show(cb, *await _balance(cb.from_user.id, db, chain, prices))


# ---- 상태 없이 온 텍스트 ----

@fallback_router.message(StateFilter(None), F.text)
async def fallback(message: Message) -> None:
    await message.answer(T.UNKNOWN, reply_markup=T.menu_kb())
