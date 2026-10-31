"""에너지 임대 화면: 소개 → 내 지갑 → 받는 주소 → 견적 → 주문(입금 안내) → 상태."""
from __future__ import annotations

import time

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import energy_texts as E
from bot import store
from bot.chain import TronError, TronReader
from bot.energy import LATE_SECONDS, USER_ORDERS_PER_HOUR, Desk, Quote
from bot.handlers.start import NO_PREVIEW, PRIVATE, korean, show
from bot.notify import Notifier
from bot.prices import PriceError, Prices
from core import users
from core.config import BotConfig
from core.tron import is_tron_address

QUOTE_SECONDS = 120          # 견적을 보여 준 뒤 이 시간이 지나면 주문 전에 다시 계산한다

router = Router(name="wallet_energy")
router.message.filter(PRIVATE)
# 입력 대기 중에도 /지갑 같은 명령어는 원래 메뉴로 가도록 주소 입력으로 받지 않는다
TYPED = F.text & ~F.text.regexp(r"^\s*[/／]")


class Buy(StatesGroup):
    receiver = State()
    recipient = State()


class Refund(StatesGroup):
    address = State()


async def _krw(prices: Prices) -> float | None:
    try:
        return (await prices.get()).trx_krw
    except PriceError:
        return None


async def _intro(energy: Desk, prices: Prices) -> str:
    menu = await energy.menu_prices() if energy.enabled else None
    return E.intro(menu, await _krw(prices), energy.margin, energy.enabled)


@router.message(Command("energy"))
@router.message(korean("에너지"))
async def intro_cmd(message: Message, state: FSMContext, energy: Desk, prices: Prices) -> None:
    await state.clear()
    await show(message, await _intro(energy, prices), E.intro_kb(energy.enabled))


@router.callback_query(F.data == "en")
async def intro_cb(cb: CallbackQuery, state: FSMContext, energy: Desk, prices: Prices) -> None:
    await state.clear()
    await show(cb, await _intro(energy, prices), E.intro_kb(energy.enabled))


@router.callback_query(F.data == "en:guide")
async def guide_cb(cb: CallbackQuery, energy: Desk) -> None:
    await show(cb, E.guide(LATE_SECONDS // 60), E.intro_kb(energy.enabled))


@router.callback_query(F.data == "en:faq")
async def faq_cb(cb: CallbackQuery, energy: Desk) -> None:
    await show(cb, E.faq(energy.ttl // 60, LATE_SECONDS // 60, energy.support), E.intro_kb(energy.enabled))


@router.callback_query(F.data == "en:list")
async def list_cb(cb: CallbackQuery, energy: Desk) -> None:
    await show(cb, E.orders_text(await energy.orders_of(cb.from_user.id)), E.back_kb())


# ───────────── 1단계: 내 지갑 ─────────────

@router.callback_query(F.data == "en:buy")
async def buy_cb(cb: CallbackQuery, state: FSMContext, db: aiosqlite.Connection, energy: Desk) -> None:
    if not energy.enabled:
        await cb.answer("지금은 준비 중입니다.", show_alert=True)
        return
    await users.register(db, cb.from_user.id, cb.from_user.username)     # 진행 알림을 받으려면 등록돼 있어야 한다
    o = await energy.open_order(cb.from_user.id)
    if o:
        await cb.message.answer(E.order_created(o, energy.pay_address, max(1, (o["expires_at"] - int(time.time())) // 60)),
                                reply_markup=E.order_kb(o["id"]))
        await cb.message.answer(E.pay_address_msg(energy.pay_address))
        await cb.answer()
        return
    wallets = await store.wallets_of(db, cb.from_user.id)
    await state.set_state(Buy.receiver)
    await cb.message.answer(E.ask_receiver(bool(wallets)), reply_markup=E.wallets_kb(wallets))
    await cb.answer()


async def _take_receiver(target: Message, address: str, state: FSMContext, chain: TronReader, energy: Desk) -> None:
    if not is_tron_address(address):
        await target.answer("❌ 올바른 TRON 주소가 아닙니다. <code>T</code>로 시작하는 34자리 주소를 확인해 주십시오.")
        return
    if address == energy.pay_address:
        await target.answer(E.IS_PAY)
        return
    try:
        acct = await chain.account(address)
    except TronError:
        await target.answer(E.CHECK_FAIL)
        return
    if not acct.exists:
        await target.answer(E.NOT_ACTIVE)
        return
    if acct.is_contract:
        await target.answer(E.IS_CONTRACT)
        return
    await state.set_state(Buy.recipient)
    await state.update_data(receiver=address, have=acct.energy, bandwidth=acct.bandwidth)
    await target.answer(E.ask_recipient(address, acct.energy, acct.bandwidth, acct.trx), reply_markup=E.recipient_kb())


@router.message(Buy.receiver, TYPED)
async def receiver_msg(message: Message, state: FSMContext, chain: TronReader, energy: Desk) -> None:
    await _take_receiver(message, message.text.strip(), state, chain, energy)


@router.callback_query(Buy.receiver, F.data.regexp(r"^en:w:\d+$"))
async def receiver_pick(cb: CallbackQuery, state: FSMContext, db: aiosqlite.Connection, chain: TronReader,
                        energy: Desk) -> None:
    w = await store.get_wallet(db, int(cb.data.split(":")[2]), cb.from_user.id)
    await cb.answer()
    if w:
        await _take_receiver(cb.message, w["address"], state, chain, energy)


# ───────────── 2단계: 받는 주소 → 견적 ─────────────

async def _quote(target: Message, state: FSMContext, energy: Desk, prices: Prices,
                 recipient: str | None, has_usdt: bool | None) -> None:
    data = await state.get_data()
    receiver = data["receiver"]
    need = await energy.need_for(receiver, recipient, has_usdt)
    if data.get("have", 0) >= need:
        await state.clear()
        await target.answer(E.enough(data["have"], need), reply_markup=E.back_kb())
        return
    q = await energy.quote(need)
    if q is None:
        await target.answer(E.PRICE_FAIL, reply_markup=E.back_kb())
        return
    await state.update_data(recipient=recipient, has_usdt=has_usdt, need=need, energy=q.energy, cost=q.cost,
                            price=q.price, supplier=q.supplier, burn=q.burn, quoted_at=time.time())
    await target.answer(E.quote_text(receiver, recipient, has_usdt, need, q.energy, q.price, q.burn, await _krw(prices),
                                     energy.margin, data.get("bandwidth", 0)), reply_markup=E.quote_kb())


@router.message(Buy.recipient, TYPED)
async def recipient_msg(message: Message, state: FSMContext, energy: Desk, prices: Prices) -> None:
    address = message.text.strip()
    if not is_tron_address(address):
        await message.answer("❌ 올바른 TRON 주소가 아닙니다. 모르시면 위의 버튼을 눌러 주십시오.")
        return
    if address == (await state.get_data()).get("receiver"):
        await message.answer(E.SAME_ADDRESS)
        return
    await _quote(message, state, energy, prices, address, None)


@router.callback_query(Buy.recipient, F.data.in_({"en:r:1", "en:r:0"}))
async def recipient_pick(cb: CallbackQuery, state: FSMContext, energy: Desk, prices: Prices) -> None:
    await cb.answer()
    await _quote(cb.message, state, energy, prices, None, cb.data == "en:r:1")


# ───────────── 3단계: 주문 ─────────────

@router.callback_query(Buy.recipient, F.data == "en:ok")
async def confirm_cb(cb: CallbackQuery, state: FSMContext, energy: Desk, prices: Prices) -> None:
    data = await state.get_data()
    if "price" not in data:
        await cb.answer()
        return
    now = time.time()
    if now - data["quoted_at"] > QUOTE_SECONDS:
        await cb.answer()
        await cb.message.answer(E.EXPIRED_QUOTE)
        await _quote(cb.message, state, energy, prices, data.get("recipient"), data.get("has_usdt"))
        return
    if await energy.recent_count(cb.from_user.id, now) >= USER_ORDERS_PER_HOUR:
        await cb.answer(E.TOO_MANY.replace("<b>", "").replace("</b>", ""), show_alert=True)
        return
    q = Quote(data["energy"], data["cost"], data["price"], data["supplier"], data["burn"])
    o = await energy.create(cb.from_user.id, data["receiver"], data["need"], q, now)
    if o is None:
        await cb.answer(E.SLOTS_FULL.replace("<b>", "").replace("</b>", ""), show_alert=True)
        return
    await state.clear()
    await cb.answer()
    await cb.message.answer(E.order_created(o, energy.pay_address, energy.ttl // 60), reply_markup=E.order_kb(o["id"]))
    await cb.message.answer(E.pay_address_msg(energy.pay_address))


# ───────────── 주문 상태 ─────────────

@router.callback_query(F.data.regexp(r"^en:o:\d+$"))
async def status_cb(cb: CallbackQuery, energy: Desk) -> None:
    o = await energy.get(int(cb.data.split(":")[2]))
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer("주문을 찾을 수 없습니다.", show_alert=True)
        return
    kb = E.order_kb(o["id"]) if o["status"] == "awaiting" else E.status_kb(o["id"])
    await show(cb, E.order_status(o, energy.pay_address, time.time()), kb)


@router.callback_query(F.data.regexp(r"^en:x:\d+$"))
async def cancel_cb(cb: CallbackQuery, energy: Desk) -> None:
    oid = int(cb.data.split(":")[2])
    if await energy.cancel(oid, cb.from_user.id):
        await show(cb, f"❌ 주문 #{oid}을 취소했습니다. <b>이 금액은 보내지 마십시오.</b>", E.back_kb())
    else:
        await cb.answer("이미 처리 중이거나 끝난 주문입니다.", show_alert=True)


# ───────────── 환불 주소 ─────────────

@router.callback_query(F.data.regexp(r"^en:rf:\d+$"))
async def refund_cb(cb: CallbackQuery, state: FSMContext, energy: Desk) -> None:
    o = await energy.get(int(cb.data.split(":")[2]))
    if not o or o["user_id"] != cb.from_user.id:
        await cb.answer("주문을 찾을 수 없습니다.", show_alert=True)
        return
    if not await energy.can_refund(o):
        await cb.answer()
        await cb.message.answer(E.refund_not_allowed(energy.support))
        return
    await state.set_state(Refund.address)
    await state.update_data(order_id=o["id"])
    await cb.message.answer(E.ASK_REFUND)
    await cb.answer()


@router.message(Refund.address, TYPED)
async def refund_msg(message: Message, state: FSMContext, energy: Desk) -> None:
    address = message.text.strip()
    if not is_tron_address(address):
        await message.answer("❌ 올바른 TRON 주소가 아닙니다. <code>T</code>로 시작하는 34자리 주소를 확인해 주십시오.")
        return
    if address == energy.pay_address:
        await message.answer(E.IS_PAY)
        return
    oid = (await state.get_data())["order_id"]
    await state.clear()
    o = await energy.set_refund(oid, message.from_user.id, address)
    await message.answer(E.refund_saved(o) if o else E.refund_not_allowed(energy.support), reply_markup=E.back_kb())


# ───────────── 끝난 단계의 버튼 ─────────────

@router.callback_query(F.data.regexp(r"^en:(ok|r:[01]|w:\d+)$"))
async def stale_cb(cb: CallbackQuery) -> None:
    """견적·주소 단계가 끝났거나(주문 완료, 취소, 재시작) 다른 단계에서 누른 옛 버튼."""
    await cb.answer(E.STALE, show_alert=True)


# ───────────── 관리자 ─────────────

@router.message(Command("energy_admin"))
async def admin_cmd(message: Message, cfg: BotConfig, energy: Desk) -> None:
    if message.from_user.id not in cfg.admin_ids:
        return
    stats = await energy.stats(int(time.time()) - 86400)
    await message.answer(E.admin_summary(stats, energy.balances, await energy.attention(), list(energy.suppliers),
                                         energy.pay_address or "-"), link_preview_options=NO_PREVIEW)


@router.message(Command("energy_done"))
async def admin_done(message: Message, command: CommandObject, cfg: BotConfig, energy: Desk) -> None:
    if message.from_user.id not in cfg.admin_ids:
        return
    parts = (command.args or "").split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await message.answer("사용법: <code>/energy_done 주문번호 메모</code>")
        return
    o = await energy.resolve(int(parts[0]), parts[1] if len(parts) > 1 else "")
    await message.answer(f"☑️ 주문 #{parts[0]}을 처리됨으로 바꾸고 손님에게 알렸습니다." if o
                         else "실패·확인 필요 상태인 주문이 아닙니다.")


@router.message(Command("energy_msg"))
async def admin_msg(message: Message, command: CommandObject, cfg: BotConfig, energy: Desk, notifier: Notifier) -> None:
    if message.from_user.id not in cfg.admin_ids:
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer("사용법: <code>/energy_msg 주문번호 내용</code>")
        return
    o = await energy.get(int(parts[0]))
    if not o:
        await message.answer("주문을 찾을 수 없습니다.")
        return
    kb = E.refund_kb(o["id"]) if await energy.can_refund(o) else None
    ok = await notifier.send(o["user_id"], E.admin_message(o, parts[1]), kb)
    await message.answer("📩 보냈습니다." if ok else "⚠️ 보내지 못했습니다(손님이 봇을 차단했을 수 있습니다).")
