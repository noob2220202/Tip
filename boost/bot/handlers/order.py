"""구매 흐름: 상품 선택 → 대상 링크 → 보내는 지갑 주소 → 주문 생성(입금 안내) / 내 주문 / 취소."""
from __future__ import annotations

import re
import time

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import money, orders
from bot.addresses import is_tron_address, parse_target
from bot.catalog import Catalog
from bot.handlers.common import NO_PREVIEW, STATUS_LABEL, hhmm, menu_keyboard, order_line
from core.style import esc
from core.texts import Texts

router = Router(name="boost_order")


class Buy(StatesGroup):
    qty = State()
    link = State()
    address = State()


def _product_list(catalog: Catalog) -> str:
    lines = []
    for p in catalog.sellable():
        days = f" · {p.days}일" if p.days else ""
        desc = f"\n   <i>{esc(p.description)}</i>" if p.description else ""
        lines.append(f"• <b>{esc(p.name)}</b> — 개당 <b>{money.fmt(p.price_micro)} USDT</b>{days}{desc}")
    return "\n".join(lines)


async def show_products(target: Message, catalog: Catalog, texts: Texts) -> None:
    products = catalog.sellable()
    if not products:
        await target.answer(texts.get("no_products"))
        return
    rows = [[InlineKeyboardButton(text=f"{p.name} · 개당 {money.fmt(p.price_micro)} USDT", callback_data=f"p:{p.id}")]
            for p in products]
    await target.answer(texts.get("choose_product", list=_product_list(catalog)),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("buy"))
async def buy_cmd(message: Message, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    await state.clear()
    await show_products(message, catalog, texts)


@router.callback_query(F.data == "buy")
async def buy_cb(cb: CallbackQuery, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    await state.clear()
    await show_products(cb.message, catalog, texts)
    await cb.answer()


@router.callback_query(F.data.startswith("p:"))
async def pick_product(cb: CallbackQuery, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    p = catalog.get(cb.data[2:])
    if not p or not p.enabled:
        await cb.answer("판매하지 않는 상품입니다.", show_alert=True)
        return
    await state.set_state(Buy.qty)
    await state.update_data(product_id=p.id)
    await cb.message.answer(texts.get("ask_qty", product=esc(p.name), unit=money.fmt(p.price_micro),
                                      min=catalog.min_qty, max=catalog.max_qty))
    await cb.answer()


def parse_qty(text: str) -> int | None:
    t = text.strip().replace(",", "").replace(" ", "")
    return int(t) if re.fullmatch(r"\d{1,6}", t) else None


@router.message(Buy.qty, F.text)
async def got_qty(message: Message, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    n = parse_qty(message.text)
    if n is None or not catalog.min_qty <= n <= catalog.max_qty:
        await message.answer(texts.get("bad_qty", min=catalog.min_qty, max=catalog.max_qty))
        return
    p = catalog.get((await state.get_data()).get("product_id", ""))
    if not p or not p.enabled:
        await state.clear()
        await message.answer(texts.get("error"), reply_markup=menu_keyboard(texts))
        return
    await state.update_data(qty=n)
    await state.set_state(Buy.link)
    await message.answer(texts.get("ask_link", product=esc(p.name), qty=n, total=money.fmt(p.price_micro * n)),
                         link_preview_options=NO_PREVIEW)


@router.message(Buy.link, F.text)
async def got_link(message: Message, texts: Texts, state: FSMContext) -> None:
    link = parse_target(message.text)
    if not link:
        await message.answer(texts.get("bad_link"))
        return
    await state.update_data(link=link)
    await state.set_state(Buy.address)
    await message.answer(texts.get("ask_addr"))


@router.message(Buy.address, F.text)
async def got_address(message: Message, db: aiosqlite.Connection, catalog: Catalog, texts: Texts,
                      state: FSMContext, wallet: str, ttl_min: int) -> None:
    addr = message.text.strip()
    if not is_tron_address(addr):
        await message.answer(texts.get("bad_addr"))
        return
    if addr == wallet:
        await message.answer(texts.get("same_as_wallet"))
        return
    data = await state.get_data()
    p = catalog.get(data.get("product_id", ""))
    qty = data.get("qty")
    if not p or not p.enabled or not data.get("link") or not isinstance(qty, int) \
            or not catalog.min_qty <= qty <= catalog.max_qty:
        await state.clear()
        await message.answer(texts.get("error"), reply_markup=menu_keyboard(texts))
        return
    now = int(time.time())
    oid = await orders.create(db, user_id=message.from_user.id, product=p, quantity=qty, link=data["link"], from_addr=addr,
                              ttl_seconds=ttl_min * 60, now=now)
    await state.clear()
    if oid is None:
        await message.answer(texts.get("too_many_open"), reply_markup=menu_keyboard(texts))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.get("cancel_button"), callback_data=f"c:{oid}")]])
    await message.answer(
        texts.get("order_created", id=oid, product=esc(p.name), qty=qty, link=esc(data["link"]), from_addr=addr,
                  amount=money.fmt(p.price_micro * qty), ttl=ttl_min, deadline=hhmm(now + ttl_min * 60)),
        link_preview_options=NO_PREVIEW, reply_markup=kb,
    )
    await message.answer(texts.get("order_wallet", wallet=wallet))      # 복사하기 쉽게 지갑 주소만 따로 보낸다


@router.callback_query(F.data.startswith("c:"))
async def cancel_order(cb: CallbackQuery, db: aiosqlite.Connection, texts: Texts) -> None:
    try:
        oid = int(cb.data[2:])
    except ValueError:
        await cb.answer()
        return
    o = await orders.get(db, oid)
    if not o or o["user_id"] != cb.from_user.id or not await orders.transition(db, oid, orders.CANCELLABLE, "cancelled"):
        await cb.answer(texts.get("cancel_failed"), show_alert=True)
        return
    await cb.message.answer(texts.get("order_cancelled", id=oid))
    await cb.answer()


async def _orders_list(target: Message, user_id: int, db: aiosqlite.Connection, texts: Texts) -> None:
    rows = await orders.by_user(db, user_id)
    if not rows:
        await target.answer(texts.get("no_orders"), reply_markup=menu_keyboard(texts))
        return
    buttons = [[InlineKeyboardButton(text=f"#{o['id']} {o['product_name']}", callback_data=f"o:{o['id']}")] for o in rows]
    await target.answer(texts.get("orders_head", list="\n".join(order_line(o) for o in rows)),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(Command("orders"))
async def orders_cmd(message: Message, db: aiosqlite.Connection, texts: Texts) -> None:
    await _orders_list(message, message.from_user.id, db, texts)


@router.callback_query(F.data == "orders")
async def orders_cb(cb: CallbackQuery, db: aiosqlite.Connection, texts: Texts) -> None:
    await _orders_list(cb.message, cb.from_user.id, db, texts)
    await cb.answer()


@router.callback_query(F.data.startswith("o:"))
async def order_detail(cb: CallbackQuery, db: aiosqlite.Connection, texts: Texts, wallet: str) -> None:
    try:
        o = await orders.get(db, int(cb.data[2:]))
    except ValueError:
        o = None
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer()
        return
    extra, kb = "", None
    if o["status"] == "awaiting_payment":
        extra = (f"\n\n💳 금액: <code>{money.fmt(o['amount_micro'])}</code> USDT\n"
                 f"👛 보내는 주소: <code>{o['from_addr']}</code>\n⏰ 마감: {hhmm(o['expires_at'])} KST\n\n"
                 f"👇 <b>입금하실 지갑 주소</b>\n<code>{wallet}</code>")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.get("cancel_button"), callback_data=f"c:{o['id']}")]])
    await cb.message.answer(
        texts.get("order_detail", id=o["id"], product=esc(o["product_name"]), qty=o["quantity"], link=esc(o["link"]),
                  amount=money.fmt(o["amount_micro"]), status=STATUS_LABEL.get(o["status"], o["status"]), extra=extra),
        link_preview_options=NO_PREVIEW, reply_markup=kb,
    )
    await cb.answer()


# 상태가 없을 때 들어온 일반 텍스트는 메뉴를 보여준다 (명령어는 제외)
@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def fallback(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("menu_title"), reply_markup=menu_keyboard(texts))
