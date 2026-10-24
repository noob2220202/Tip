"""10레벨 부스트 계산기: 구독자 수 + 현재 부스트 → 추가로 필요한 개수와 가장 싼 상품 조합."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import levels, money
from bot.catalog import Catalog
from core.style import esc
from core.texts import Texts

router = Router(name="boost_calc")


class Calc(StatesGroup):
    subs = State()
    current = State()


def parse_int(text: str) -> int | None:
    t = text.strip().replace(",", "").replace(" ", "")
    return int(t) if re.fullmatch(r"\d{1,9}", t) else None


async def begin(target: Message, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    await state.set_state(Calc.subs)
    await target.answer(texts.get("calc_ask_subs", target=catalog.level_target))


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
    await message.answer(texts.get("calc_ask_cur", subs=f"{n:,}"))


@router.message(Calc.current, F.text)
async def got_current(message: Message, catalog: Catalog, texts: Texts, state: FSMContext) -> None:
    cur = parse_int(message.text)
    if cur is None:
        await message.answer(texts.get("calc_bad_number"))
        return
    subs = (await state.get_data())["subs"]
    await state.clear()
    rule = levels.LevelRule(catalog.level_target, catalog.level_divisor)
    need = levels.boosts_required(rule.target, subs, rule)
    left = max(need - cur, 0)
    common = dict(target=rule.target, subs=f"{subs:,}", need=f"{need:,}", cur=f"{cur:,}")
    if left == 0:
        await message.answer(texts.get("calc_reached", **common))
        return
    products = catalog.sellable()
    if left > levels.MAX_NEED:
        bundle_text = texts.get("calc_too_big")
    else:
        b = levels.best_bundle(products, left)
        if b is None:
            bundle_text = texts.get("calc_no_bundle")
        else:
            lines = "\n".join(f"• {esc(p.name)} × <b>{n}</b>" for p, n in b.items)
            bundle_text = texts.get("calc_bundle", lines=lines, boosts=f"{b.boosts:,}", cost=money.fmt(b.cost_micro))
    kb = None
    if products:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.get("order_now_button"), callback_data="buy")]])
    await message.answer(texts.get("calc_result", left=f"{left:,}", bundle=bundle_text + "\n" if bundle_text else "", **common),
                         reply_markup=kb)
