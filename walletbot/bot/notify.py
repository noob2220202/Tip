"""사용자에게 알림을 보낸다. 차단한 사용자는 비활성 처리해서 더 조회·발송하지 않는다."""
from __future__ import annotations

import asyncio
import logging

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

from core import users

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, bot: Bot, db: aiosqlite.Connection, admin_ids=()):
        self.bot, self.db, self.admin_ids = bot, db, tuple(admin_ids)

    async def send_id(self, user_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> int | None:
        """보낸 메시지 id. 사용자가 봇을 막았으면 비활성 처리하고 None(다시 시도하지 않는다). 그 밖의 실패도 None."""
        for attempt in range(2):
            try:
                msg = await self.bot.send_message(user_id, text, reply_markup=markup,
                                                  link_preview_options=LinkPreviewOptions(is_disabled=True))
                return msg.message_id
            except TelegramForbiddenError:
                await users.set_active(self.db, user_id, False)
                return None
            except TelegramRetryAfter as e:
                if attempt:
                    return None
                await asyncio.sleep(min(e.retry_after, 30))
            except TelegramAPIError as e:
                log.warning("알림 전송 실패 user=%s: %s", user_id, e)
                return None
        return None

    async def send(self, user_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
        return await self.send_id(user_id, text, markup) is not None

    async def admins(self, text: str) -> None:
        for a in self.admin_ids:
            await self.send(a, text)
