"""구독 게이트: 필수 채널에 모두 가입해야 봇을 쓸 수 있다."""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)

from core import users
from core.config import BotConfig, Channel
from core.texts import Texts

log = logging.getLogger(__name__)

CHECK_DATA = "gate:check"
JOINED = {"creator", "administrator", "member"}


class SubscriptionGate(BaseMiddleware):
    """dp.update 바깥 미들웨어. 모든 update 유형에 적용한다."""

    def __init__(self, cfg: BotConfig, db: aiosqlite.Connection, texts: Texts):
        self.cfg = cfg
        self.db = db
        self.texts = texts
        self.dp: Dispatcher | None = None
        # (user_id, chat_id) -> 가입 확인 시각. 가입 확인된 결과만 캐시한다.
        self._ok_cache: dict[tuple[int, str | int], float] = {}
        # 게이트에 막힌 마지막 메시지. 가입 확인을 통과하면 이어서 처리한다.
        self._pending: dict[int, Update] = {}

    async def _is_member(self, bot: Bot, user_id: int, ch: Channel) -> bool:
        key = (user_id, ch.chat_id)
        now = time.monotonic()
        if now - self._ok_cache.get(key, -1e9) < self.cfg.common.cache_ttl:
            return True
        try:
            m = await bot.get_chat_member(ch.chat_id, user_id)
        except TelegramAPIError as e:
            log.warning("getChatMember 실패 chat=%s: %s", ch.chat_id, e)
            return self.cfg.common.fail_mode == "allow"
        joined = m.status in JOINED or (m.status == "restricted" and getattr(m, "is_member", False))
        if joined:
            self._ok_cache[key] = now
        return joined

    async def missing(self, bot: Bot, user_id: int) -> list[Channel]:
        return [ch for ch in self.cfg.common.channels if not await self._is_member(bot, user_id, ch)]

    def _keyboard(self, missing: list[Channel]) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton(text=ch.title or str(ch.chat_id), url=ch.url)] for ch in missing]
        rows.append([InlineKeyboardButton(text=self.texts.get("gate_button"), callback_data=CHECK_DATA)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        bot: Bot = data["bot"]
        if user is None or not self.cfg.common.channels:
            return await handler(event, data)

        # /start는 안내 전에 먼저 등록한다
        msg = event.message
        if msg and msg.text and msg.text.split("@")[0].split()[0] == "/start":
            await users.register(self.db, user.id, user.username)

        # 차단/해제 감지 업데이트는 게이트를 거치지 않는다
        if event.my_chat_member or event.chat_member:
            return await handler(event, data)

        if self.cfg.common.exempt_admins and user.id in self.cfg.admin_ids:
            return await handler(event, data)

        # "가입했어요" 버튼은 게이트가 직접 처리한다
        cb = event.callback_query
        if cb and cb.data == CHECK_DATA:
            return await self._on_check(cb, bot, user.id)

        missing = await self.missing(bot, user.id)
        if not missing:
            return await handler(event, data)

        # 막힌 요청을 기억해 두고 안내한다
        if msg:
            self._pending[user.id] = event
            await msg.answer(
                self.texts.get("gate_intro", count=len(missing)),
                reply_markup=self._keyboard(missing),
            )
        elif cb:
            await cb.answer(self.texts.get("gate_missing", missing=", ".join(c.title or str(c.chat_id) for c in missing)), show_alert=True)
        return None

    async def _on_check(self, cb: CallbackQuery, bot: Bot, user_id: int) -> None:
        # 확인 버튼은 항상 최신 상태를 보도록 캐시를 비운다
        for key in [k for k in self._ok_cache if k[0] == user_id]:
            del self._ok_cache[key]
        missing = await self.missing(bot, user_id)
        if missing:
            names = ", ".join(c.title or str(c.chat_id) for c in missing)
            await cb.answer(self.texts.get("gate_missing", missing=names), show_alert=True)
            return
        await cb.answer(self.texts.get("gate_ok"))
        if isinstance(cb.message, Message):
            try:
                await cb.message.delete()
            except TelegramAPIError:
                pass
        pending = self._pending.pop(user_id, None)
        if pending is not None and self.dp is not None:
            await self.dp.feed_update(bot, pending)


async def self_check(bot: Bot, cfg: BotConfig) -> list[str]:
    """봇이 필수 채널의 관리자인지 점검한다. 관리자가 아니면 가입 여부를 확인할 수 없다.

    문제가 있는 채널마다 사람이 읽을 수 있는 설명을 돌려준다 (없으면 빈 리스트).
    """
    problems: list[str] = []
    if not cfg.common.channels:
        return problems
    me = await bot.me()
    for ch in cfg.common.channels:
        try:
            m = await bot.get_chat_member(ch.chat_id, me.id)
        except TelegramAPIError as e:
            problems.append(f"{ch.chat_id}: 채널을 조회할 수 없습니다 ({e}). 채널 주소와 봇 초대 여부를 확인해 주십시오.")
            continue
        if m.status not in ("administrator", "creator"):
            problems.append(f"{ch.chat_id}: 봇이 관리자가 아닙니다(현재 {m.status}). 채널 관리자로 추가해 주십시오.")
    return problems
