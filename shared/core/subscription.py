"""구독 게이트: 필수 채널에 모두 가입해야 봇을 쓸 수 있다."""
from __future__ import annotations

import asyncio
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
from core.verifier import VerifierClient, VerifierError, channel_problems, is_joined

log = logging.getLogger(__name__)

CHECK_DATA = "gate:check"


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
        self.verifier = make_verifier(cfg)

    def _cached_ok(self, user_id: int, ch: Channel) -> bool:
        return time.monotonic() - self._ok_cache.get((user_id, ch.chat_id), -1e9) < self.cfg.common.cache_ttl

    async def _is_member(self, bot: Bot, user_id: int, ch: Channel) -> bool:
        """검수 서비스를 쓰지 않을 때: 이 봇이 직접 getChatMember로 확인한다."""
        try:
            m = await bot.get_chat_member(ch.chat_id, user_id)
        except TelegramAPIError as e:
            log.warning("getChatMember 실패 chat=%s: %s", ch.chat_id, e)
            return self.cfg.common.fail_mode == "allow"
        return is_joined(m)

    async def missing(self, bot: Bot, user_id: int) -> list[Channel]:
        todo = [ch for ch in self.cfg.common.channels if not self._cached_ok(user_id, ch)]
        if not todo:
            return []
        block = self.cfg.common.fail_mode == "block"
        out: list[Channel] = []
        if self.verifier is not None:
            try:
                res = await self.verifier.check(user_id)
            except VerifierError as e:
                log.error("%s", e)
                return todo if block else []
            gone, failed = set(res.get("missing", [])), set(res.get("errors", []))
            for ch in todo:
                cid = str(ch.chat_id)
                if cid in gone or (cid in failed and block):
                    out.append(ch)
                elif cid not in failed:
                    self._ok_cache[(user_id, ch.chat_id)] = time.monotonic()
            return out
        for ch in todo:
            if await self._is_member(bot, user_id, ch):
                self._ok_cache[(user_id, ch.chat_id)] = time.monotonic()
            else:
                out.append(ch)
        return out

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


def make_verifier(cfg: BotConfig) -> VerifierClient | None:
    if not cfg.common.verifier_url or not cfg.common.channels:
        return None
    return VerifierClient(cfg.common.verifier_url, cfg.env.get("VERIFY_SECRET", ""))


async def self_check(bot: Bot, cfg: BotConfig, direct: bool = False) -> list[str]:
    """필수 채널 설정이 동작하는지 점검한다. 문제가 있는 항목마다 설명을 돌려준다(없으면 빈 리스트).

    검수 서비스를 쓰면 검수 봇이 채널 관리자인지를 서비스에 물어본다. 검수 봇은 시작 직후라
    아직 안 떠 있을 수 있어서 몇 번 재시도한다. direct=True면 이 봇 자신을 점검한다(검수 봇용).
    """
    if not cfg.common.channels:
        return []
    verifier = None if direct else make_verifier(cfg)
    if verifier is None:
        return await channel_problems(bot, cfg.common.channels)
    err = ""
    for _ in range(5):
        try:
            return await verifier.health()
        except VerifierError as e:
            err = str(e)
            await asyncio.sleep(3)
    return [f"검수 서비스: {err}. 검수 봇이 실행 중인지, 이 봇의 .env에 VERIFY_SECRET이 검수 봇과 같게 들어 있는지 확인해 주십시오."]
