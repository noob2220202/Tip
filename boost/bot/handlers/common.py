from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from bot import money
from core.style import esc
from core.texts import Texts

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

STATUS_LABEL = {
    "awaiting_payment": "💳 입금 대기", "paid": "✅ 입금 확인", "submitting": "📨 접수 중",
    "processing": "🚀 진행 중", "completed": "🎉 완료", "expired": "⌛ 만료",
    "cancelled": "🗑 취소", "manual_review": "🛠 관리자 확인 중",
}


def menu_keyboard(texts: Texts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.get("menu_buy"), callback_data="buy")],
        [InlineKeyboardButton(text=texts.get("menu_calc"), callback_data="calc")],
        [InlineKeyboardButton(text=texts.get("menu_orders"), callback_data="orders")],
    ])


KST = timezone(timedelta(hours=9))   # 한국은 서머타임이 없어서 고정 오프셋으로 충분하다 (서버 시간대와 tzdata에 의존하지 않음)


def hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%H:%M")


def md_hhmm(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%m-%d %H:%M") if ts else "-"


def order_line(o) -> str:
    return (f"<b>#{o['id']}</b> {esc(o['product_name'])} ×{o['quantity']} · {money.fmt(o['amount_micro'])} USDT · "
            f"{STATUS_LABEL.get(o['status'], o['status'])}")
