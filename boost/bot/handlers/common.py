from __future__ import annotations

import time

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


def hhmm(ts: int) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def order_line(o) -> str:
    return (f"<b>#{o['id']}</b> {esc(o['product_name'])} · {money.fmt(o['amount_micro'])} USDT · "
            f"{STATUS_LABEL.get(o['status'], o['status'])}")
