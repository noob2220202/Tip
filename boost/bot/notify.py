"""워커 이벤트를 사용자·관리자 메시지로 바꿔 보낸다."""
from __future__ import annotations

import logging

from aiogram import Bot

from bot.payments import Event
from core.style import esc
from core.texts import Texts

log = logging.getLogger(__name__)

# 관리자에게도 알려야 하는 이벤트
ADMIN_TEXT = {
    "late": "⏰ 기한 후(또는 취소 후) 입금이 확인되었습니다. 주문 #{id} · tx <code>{detail}</code>\n/approve {id} 또는 /reject {id}",
    "mismatch": "⚠️ 금액이 다른 입금이 있습니다. 주문 #{id} · {detail}\n/approve {id} 로 승인하거나 /reject {id}",
    "panel_failed": "🚨 패널 처리 실패. 주문 #{id} · {detail}\n/retry {id} 또는 /reject {id}",
    "panel_uncertain": "🚨 패널 접수 여부가 불확실합니다. 주문 #{id} · {detail}\n패널에서 확인한 뒤 /approve {id}(재접수) 또는 /reject {id}",
}
USER_KEY = {
    "paid": "n_paid", "submitted": "n_submitted", "completed": "n_completed",
    "late": "n_review", "mismatch": "n_mismatch", "panel_failed": "n_review", "panel_uncertain": "n_review",
}


class Notifier:
    def __init__(self, bot: Bot, texts: Texts, admin_ids: frozenset[int]) -> None:
        self.bot, self.texts, self.admin_ids = bot, texts, admin_ids

    async def alert(self, text: str) -> None:
        for admin in self.admin_ids:
            try:
                await self.bot.send_message(admin, text)
            except Exception:  # noqa: BLE001
                log.warning("관리자 %s에게 알림을 보내지 못했습니다", admin)

    async def event(self, ev: Event) -> None:
        key = USER_KEY.get(ev.kind)
        if key:
            try:
                await self.bot.send_message(ev.user_id, self.texts.get(key, id=ev.order_id))
            except Exception:  # noqa: BLE001 - 사용자가 봇을 차단했을 수 있다
                log.warning("사용자 %s에게 알림을 보내지 못했습니다", ev.user_id)
        if ev.kind in ADMIN_TEXT:
            await self.alert(ADMIN_TEXT[ev.kind].format(id=ev.order_id, detail=esc(ev.detail)))
