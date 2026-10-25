"""알림 설정: 최소 알림 금액, 김프 조건, 정기 시세."""
from __future__ import annotations

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import money, store
from bot import texts as T
from bot.handlers.start import PRIVATE, korean, show
from bot.prices import PriceError, Prices
from core.clock import parse_time

PCT_RANGE = (-30.0, 100.0)

router = Router(name="wallet_alerts")
router.message.filter(PRIVATE)


class Set(StatesGroup):
    minimum = State()
    rule = State()
    time = State()


async def _main(user_id: int, db: aiosqlite.Connection) -> tuple[str, object]:
    s = await store.get_settings(db, user_id)
    return (T.alerts_text(s, await store.rules_of(db, user_id), await store.daily_times(db, user_id)),
            T.alerts_kb(s, bool(s["daily_on"])))


@router.message(Command("alerts"))
@router.message(korean("알림|설정"))
async def alerts_cmd(message: Message, db: aiosqlite.Connection, state: FSMContext) -> None:
    await state.clear()
    await show(message, *await _main(message.from_user.id, db))


@router.callback_query(F.data == "al")
async def alerts_cb(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    await state.clear()
    await show(cb, *await _main(cb.from_user.id, db))


# ---- 최소 알림 금액 ----

@router.callback_query(F.data.regexp(r"^mn:(USDT|TRX)$"))
async def min_ask(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    token = cb.data.split(":")[1]
    s = await store.get_settings(db, cb.from_user.id)
    await state.set_state(Set.minimum)
    await state.update_data(token=token)
    await cb.message.answer(T.asks("min", token=token, cur=money.fmt(s["min_usdt"] if token == "USDT" else s["min_trx"])))
    await cb.answer()


@router.message(Set.minimum, F.text)
async def min_do(message: Message, db: aiosqlite.Connection, state: FSMContext) -> None:
    micro = money.parse(message.text)
    if micro is None or micro > 1_000_000 * money.UNIT:
        await message.answer("0 이상의 숫자로 다시 보내 주십시오. (예: <code>0.5</code>)")
        return
    token = (await state.get_data())["token"]
    await state.clear()
    await store.set_min(db, message.from_user.id, token, micro)
    text, kb = await _main(message.from_user.id, db)
    await show(message, "✅ 바꿨습니다.\n\n" + text, kb)


# ---- 김프 조건 ----

async def _rules(user_id: int, db: aiosqlite.Connection, prices: Prices) -> tuple[str, object]:
    rules = await store.rules_of(db, user_id)
    try:
        kimp = (await prices.get()).kimp
    except PriceError:
        kimp = None
    return T.rules_text(rules, kimp), T.rules_kb(rules, len(rules) < store.MAX_RULES_PER_USER)


@router.callback_query(F.data == "kr")
async def rules_cb(cb: CallbackQuery, db: aiosqlite.Connection, prices: Prices, state: FSMContext) -> None:
    await state.clear()
    await show(cb, *await _rules(cb.from_user.id, db, prices))


@router.callback_query(F.data.regexp(r"^kr\+:(up|down)$"))
async def rule_ask(cb: CallbackQuery, db: aiosqlite.Connection, prices: Prices, state: FSMContext) -> None:
    if len(await store.rules_of(db, cb.from_user.id)) >= store.MAX_RULES_PER_USER:
        await cb.answer("조건은 최대 3개까지 등록할 수 있습니다.", show_alert=True)
        return
    try:
        k = (await prices.get()).kimp
    except PriceError:
        k = None
    await state.set_state(Set.rule)
    await state.update_data(direction=cb.data.split(":")[1])
    await cb.message.answer(T.asks("rule", now="알 수 없음" if k is None else f"{k:+.2f}%"))
    await cb.answer()


@router.message(Set.rule, F.text)
async def rule_do(message: Message, db: aiosqlite.Connection, prices: Prices, state: FSMContext) -> None:
    try:
        pct = round(float(message.text.strip().replace("%", "")), 2)
        if not PCT_RANGE[0] <= pct <= PCT_RANGE[1] or pct != pct:
            raise ValueError
    except ValueError:
        await message.answer(f"{PCT_RANGE[0]:g} ~ {PCT_RANGE[1]:g} 사이의 숫자로 다시 보내 주십시오. (예: <code>3</code>)")
        return
    direction = (await state.get_data())["direction"]
    await state.clear()
    if len(await store.rules_of(db, message.from_user.id)) >= store.MAX_RULES_PER_USER:
        await message.answer("조건은 최대 3개까지 등록할 수 있습니다.")
        return
    await store.add_rule(db, message.from_user.id, direction, pct)
    text, kb = await _rules(message.from_user.id, db, prices)
    await show(message, "✅ 등록했습니다.\n\n" + text, kb)


@router.callback_query(F.data.regexp(r"^kr-:\d+$"))
async def rule_delete(cb: CallbackQuery, db: aiosqlite.Connection, prices: Prices) -> None:
    await store.delete_rule(db, int(cb.data.split(":")[1]), cb.from_user.id)
    await show(cb, *await _rules(cb.from_user.id, db, prices))


# ---- 정기 시세 ----

async def _daily(user_id: int, db: aiosqlite.Connection) -> tuple[str, object]:
    s = await store.get_settings(db, user_id)
    times = await store.daily_times(db, user_id)
    body = ", ".join(t["hhmm"] for t in times) or "등록된 시각이 없습니다."
    text = (f"🕘 <b>정기 시세</b> — <b>{'ON' if s['daily_on'] else 'OFF'}</b>\n\n<blockquote>{body}</blockquote>\n\n"
            "정한 시각(한국 시간)에 시세와 김프를 보내 드립니다. (최대 3개)")
    return text, T.daily_kb(times, bool(s["daily_on"]), len(times) < store.MAX_DAILY_TIMES)


@router.callback_query(F.data == "dl")
async def daily_cb(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    await state.clear()
    await show(cb, *await _daily(cb.from_user.id, db))


@router.callback_query(F.data.regexp(r"^dl:(on|off)$"))
async def daily_toggle(cb: CallbackQuery, db: aiosqlite.Connection) -> None:
    on = cb.data.endswith("on")
    if on and not await store.daily_times(db, cb.from_user.id):
        await cb.answer("먼저 시각을 하나 이상 추가해 주십시오.", show_alert=True)
        return
    await store.set_daily(db, cb.from_user.id, on)
    await show(cb, *await _daily(cb.from_user.id, db))


@router.callback_query(F.data == "dl+")
async def time_ask(cb: CallbackQuery, db: aiosqlite.Connection, state: FSMContext) -> None:
    if len(await store.daily_times(db, cb.from_user.id)) >= store.MAX_DAILY_TIMES:
        await cb.answer("시각은 최대 3개까지 등록할 수 있습니다.", show_alert=True)
        return
    await state.set_state(Set.time)
    await cb.message.answer(T.asks("time"))
    await cb.answer()


@router.message(Set.time, F.text)
async def time_do(message: Message, db: aiosqlite.Connection, state: FSMContext) -> None:
    parsed = parse_time(message.text)
    if not parsed:
        await message.answer("시간 형식을 읽지 못했습니다. <code>09:00</code> 또는 <code>오후 9시</code> 처럼 보내 주십시오.")
        return
    await state.clear()
    uid = message.from_user.id
    if len(await store.daily_times(db, uid)) >= store.MAX_DAILY_TIMES:
        await message.answer("시각은 최대 3개까지 등록할 수 있습니다.")
        return
    hhmm = f"{parsed[0]:02d}:{parsed[1]:02d}"
    if not await store.add_daily(db, uid, hhmm):
        await message.answer("이미 등록한 시각입니다.")
        return
    await store.set_daily(db, uid, True)        # 시각을 추가했다는 건 받겠다는 뜻이다
    text, kb = await _daily(uid, db)
    await show(message, "✅ 등록했습니다. 정기 시세를 켰습니다.\n\n" + text, kb)


@router.callback_query(F.data.regexp(r"^dl-:\d\d:\d\d$"))
async def time_delete(cb: CallbackQuery, db: aiosqlite.Connection) -> None:
    await store.delete_daily(db, cb.from_user.id, cb.data.split(":", 1)[1])
    await show(cb, *await _daily(cb.from_user.id, db))
