"""주간 발표. 시각이 된 봇은 회차를 넘기며 발표할 그룹을 큐에 넣고, 큐를 비우면서 실제로 보낸다."""
from __future__ import annotations

import logging
import time

import aiosqlite
from aiogram.types import LinkPreviewOptions
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter, TelegramUnauthorizedError

from bot import child_texts as T
from bot import store
from bot.fleet import Fleet

log = logging.getLogger(__name__)

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

MAX_ATTEMPTS = 5
HEALTH_EVERY = 15 * 60   # 토큰이 폐기됐는지 확인하는 주기(초)


async def announcement(db: aiosqlite.Connection, bot_row, chat_id: int, round_: int, preview: bool = False,
                       persist: bool = False) -> str:
    rows = await store.ranking(db, bot_row["bot_id"], chat_id, round_)
    prizes = store.prize_list(bot_row)
    winners = store.pick_winners(rows, prizes, bot_row["qualify"]) if prizes else []
    if persist:
        await store.save_winners(db, bot_row["bot_id"], chat_id, round_, winners)
    return T.result_text(bot_row, round_, rows, winners, preview=preview)


class Settler:
    def __init__(self, db: aiosqlite.Connection, fleet: Fleet, tick_seconds: int = 30):
        self.db, self.fleet, self.tick_seconds = db, fleet, tick_seconds
        self._last_health = 0.0

    async def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        for bot in await store.due_bots(self.db, now):
            if await store.advance_round(self.db, bot, now):
                log.info("회차 마감 bot=%s round=%s", bot["bot_id"], bot["round"])
        await self._deliver()
        if now - self._last_health >= HEALTH_EVERY:
            self._last_health = now
            await self.fleet.health()

    async def _deliver(self) -> None:
        for q in await store.queued(self.db):
            bot_id, chat_id, round_ = q["bot_id"], q["chat_id"], q["round"]
            bot_row = await store.get_bot(self.db, bot_id)
            tg = self.fleet.bots.get(bot_id)
            if not bot_row or bot_row["status"] != "active" or tg is None:
                await store.dequeue(self.db, bot_id, chat_id, round_)
                continue
            # 발표 문구의 '다음 발표' 안내는 회차를 넘긴 뒤의 일정이어야 하므로 지금 설정으로 만든다
            text = await announcement(self.db, bot_row, chat_id, round_, persist=True)
            try:
                msg = await tg.send_message(chat_id, text, reply_markup=T.kb_rank(),
                                            link_preview_options=_NO_PREVIEW)
                try:
                    await tg.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
                except TelegramAPIError:
                    pass   # 고정 권한이 없으면 조용히 넘어간다
                await store.dequeue(self.db, bot_id, chat_id, round_)
            except TelegramUnauthorizedError:
                await self.fleet.revoke(bot_id)
            except TelegramForbiddenError:
                await store.set_chat_active(self.db, bot_id, chat_id, False)   # 강퇴당함
                await store.dequeue(self.db, bot_id, chat_id, round_)
            except TelegramRetryAfter:
                pass   # 다음 틱에 다시 보낸다
            except TelegramAPIError as e:
                if "chat not found" in str(e).lower():
                    await store.set_chat_active(self.db, bot_id, chat_id, False)
                    await store.dequeue(self.db, bot_id, chat_id, round_)
                elif await store.bump_attempts(self.db, bot_id, chat_id, round_) >= MAX_ATTEMPTS:
                    log.error("발표 포기 bot=%s chat=%s round=%s: %s", bot_id, chat_id, round_, e)
                    await store.dequeue(self.db, bot_id, chat_id, round_)

