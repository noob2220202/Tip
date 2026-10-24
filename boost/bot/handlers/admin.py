"""관리자 명령: /orders_admin 대신 /list, /order, /approve, /reject, /retry, /balance, /sales."""
from __future__ import annotations

import time

import aiosqlite
from aiogram import Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

from bot import money, orders
from bot.handlers.common import STATUS_LABEL, order_line
from bot.notify import Notifier
from bot.smm import PanelRejected, PanelUncertain, SmmPanel
from core.config import BotConfig
from core.style import esc

router = Router(name="boost_admin")


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message, cfg: BotConfig) -> bool:
        return bool(message.from_user and message.from_user.id in cfg.admin_ids)


router.message.filter(IsAdmin())


def _arg_id(command: CommandObject) -> int | None:
    a = (command.args or "").split()
    return int(a[0]) if a and a[0].isdigit() else None


@router.message(Command("list"))
async def list_(message: Message, db: aiosqlite.Connection, command: CommandObject) -> None:
    status = (command.args or "").strip() or None
    rows = await orders.recent(db, status)
    if not rows:
        await message.answer("해당하는 주문이 없습니다.")
        return
    await message.answer("\n".join(order_line(o) for o in rows)
                         + "\n\n<i>/list 상태 로 거를 수 있습니다. 예) /list manual_review</i>")


@router.message(Command("order"))
async def order(message: Message, db: aiosqlite.Connection, command: CommandObject) -> None:
    oid = _arg_id(command)
    o = await orders.get(db, oid) if oid else None
    if not o:
        await message.answer("사용법: /order 주문번호")
        return
    when = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "-"  # noqa: E731
    await message.answer(
        f"🧾 <b>주문 #{o['id']}</b> · {STATUS_LABEL.get(o['status'], o['status'])}\n"
        f"<blockquote>사용자 <code>{o['user_id']}</code>\n상품 {esc(o['product_name'])} (서비스 {o['service_id']} × {o['quantity']})\n"
        f"대상 {esc(o['link'])}\n보낸 주소 <code>{o['from_addr']}</code>\n금액 {money.fmt(o['amount_micro'])} USDT\n"
        f"txid <code>{o['txid'] or '-'}</code>\n패널 주문 {o['panel_order_id'] or '-'} ({esc(o['panel_status'] or '-')})\n"
        f"생성 {when(o['created_at'])} · 입금 {when(o['paid_at'])} · 완료 {when(o['done_at'])}\n"
        f"메모 {esc(o['note'] or '-')}</blockquote>",
        disable_web_page_preview=True,
    )


@router.message(Command("approve"))
async def approve(message: Message, db: aiosqlite.Connection, command: CommandObject) -> None:
    """확인이 필요한 주문(또는 입금 대기)을 수동 승인한다 → 다음 작업 주기에 패널로 접수된다.
    패널 접수 여부가 불확실했던 주문은 패널에서 접수되지 않은 것을 확인한 뒤에만 승인할 것."""
    oid = _arg_id(command)
    if not oid:
        await message.answer("사용법: /approve 주문번호")
        return
    ok = await orders.transition(db, oid, ("manual_review", "awaiting_payment", "expired", "cancelled"), "paid",
                                 paid_at=int(time.time()), attempts=0, next_try=0,
                                 note=f"관리자 {message.from_user.id} 수동 승인")
    await message.answer("✅ 승인했습니다. 곧 패널에 접수됩니다." if ok else "승인할 수 없는 상태이거나 없는 주문입니다.")


@router.message(Command("reject"))
async def reject(message: Message, db: aiosqlite.Connection, command: CommandObject, notifier: Notifier) -> None:
    oid = _arg_id(command)
    o = await orders.get(db, oid) if oid else None
    if not o:
        await message.answer("사용법: /reject 주문번호")
        return
    ok = await orders.transition(db, oid, ("manual_review", "awaiting_payment", "expired"), "cancelled",
                                 note=f"관리자 {message.from_user.id} 거절/환불 처리")
    await message.answer("🗑 취소 처리했습니다. 환불이 필요하면 직접 송금해 주십시오." if ok else "취소할 수 없는 상태입니다.")
    if ok:
        try:
            await message.bot.send_message(o["user_id"], f"🗑 <b>주문 #{oid}이(가) 취소되었습니다.</b>\n문의가 필요하시면 관리자에게 연락해 주십시오.")
        except Exception:  # noqa: BLE001
            pass


@router.message(Command("retry"))
async def retry(message: Message, db: aiosqlite.Connection, command: CommandObject) -> None:
    oid = _arg_id(command)
    if not oid:
        await message.answer("사용법: /retry 주문번호")
        return
    ok = await orders.transition(db, oid, "manual_review", "paid", attempts=0, next_try=0, note="관리자 재시도")
    await message.answer("🔁 다시 접수 대기열에 넣었습니다." if ok else "재시도할 수 없는 상태입니다.")


@router.message(Command("balance"))
async def balance(message: Message, panel: SmmPanel) -> None:
    try:
        amount, cur = await panel.balance()
    except (PanelRejected, PanelUncertain) as e:
        await message.answer(f"❗ 패널 잔액을 확인하지 못했습니다: {esc(e)}")
        return
    await message.answer(f"💰 패널 잔액: <b>{esc(amount)} {esc(cur)}</b>")


@router.message(Command("sales"))
async def sales(message: Message, db: aiosqlite.Connection) -> None:
    s = await orders.sales(db)
    states = " · ".join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in sorted(s["by_status"].items())) or "-"
    await message.answer(
        f"📊 <b>매출 요약</b>\n<blockquote>결제된 주문 <b>{s['n']}</b>건\n"
        f"합계 <b>{money.fmt(s['micro'])} USDT</b>\n부스트 <b>{s['boosts']}</b>개</blockquote>\n{states}"
    )
