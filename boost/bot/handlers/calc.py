"""10레벨 부스트 계산기: 구독자 수 + 현재 부스트 → 추가로 필요한 개수와 가장 싼 상품 조합."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import levels
from bot.catalog import Catalog
from core.texts import Texts

router = Router(name="boost_calc")

KIND = {False: "채널", True: "그룹"}


class Calc(StatesGroup):
    kind = State()
    subs = State()
    current = State()


def parse_int(text: str) -> int | None:
    t = text.strip().replace(",", "").replace(" ", "")
    return int(t) if re.fullmatch(r"\d{1,9}", t) else None


async def begin(target: Message, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    await state.set_state(Calc.kind)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.get("calc_btn_channel"), callback_data="ck:ch"),
        InlineKeyboardButton(text=texts.get("calc_btn_group"), callback_data="ck:gr"),
    ]])
    await target.answer(texts.get("calc_ask_kind", target=catalog.level_target), reply_markup=kb)


@router.callback_query(Calc.kind, F.data.in_({"ck:ch", "ck:gr"}))
async def got_kind(cb: CallbackQuery, texts: Texts, state: FSMContext) -> None:
    group = cb.data == "ck:gr"
    await state.update_data(group=group)
    await state.set_state(Calc.subs)
    await cb.message.answer(texts.get("calc_ask_subs", kind=KIND[group], unit="멤버" if group else "구독자"))
    await cb.answer()


@router.message(Command("calc"))
async def calc_cmd(message: Message, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    await begin(message, catalog, texts, state)


@router.callback_query(F.data == "calc")
async def calc_cb(cb: CallbackQuery, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    await begin(cb.message, catalog, texts, state)
    await cb.answer()


@router.message(Calc.subs, F.text)
async def got_subs(message: Message, texts: Texts, state: FSMContext) -> None:
    n = parse_int(message.text)
    if n is None:
        await message.answer(texts.get("calc_bad_number"))
        return
    await state.update_data(subs=n)
    await state.set_state(Calc.current)
    unit = "멤버" if (await state.get_data())["group"] else "구독자"
    await message.answer(texts.get("calc_ask_cur", subs=f"{n:,}", unit=unit))


@router.message(Calc.current, F.text)
async def got_current(message: Message, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    cur = parse_int(message.text)
    if cur is None:
        await message.answer(texts.get("calc_bad_number"))
        return
    data = await state.get_data()
    subs, group = data["subs"], data["group"]
    await state.clear()
    rule = levels.LevelRule(catalog.level_target, catalog.level_divisor_group if group else catalog.level_divisor)
    need = levels.boosts_required(rule.target, subs, rule)
    left = max(need - cur, 0)
    common = dict(target=rule.target, subs=f"{subs:,}", need=f"{need:,}", cur=f"{cur:,}",
                  kind=KIND[group], unit="멤버" if group else "구독자")
    if left == 0:
        await message.answer(texts.get("calc_reached", **common))
        return
    kb = None
    if catalog.sellable():
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.get("order_now_button"), callback_data="buy")]])
    note = texts.get("calc_note", min=catalog.min_qty, max=catalog.max_qty)
    await message.answer(texts.get("calc_result", left=f"{left:,}", note=note, **common), reply_markup=kb)
