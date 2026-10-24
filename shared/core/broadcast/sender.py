"""백그라운드 발송 워커: 레이트리밋, 429 대기, 403 차단 처리, 재시작 후 재개."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core import users
from core.broadcast import models
from core.texts import Texts

log = logging.getLogger(__name__)

PROGRESS_EVERY = 3.0  # 진행률 메시지 갱신 간격(초)


def build_markup(buttons_json: str | None) -> InlineKeyboardMarkup | None:
    buttons = json.loads(buttons_json or "[]")
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=x["text"], url=x["url"]) for x in buttons]]
    )


async def send_campaign_message(bot: Bot, chat_id: int, c: aiosqlite.Row) -> None:
    markup = build_markup(c["buttons_json"])
    kind = c["content_type"]
    if kind == "text":
        await bot.send_message(chat_id, c["text"], reply_markup=markup)
    elif kind == "photo":
        await bot.send_photo(chat_id, c["file_id"], caption=c["text"], reply_markup=markup)
    elif kind == "video":
        await bot.send_video(chat_id, c["file_id"], caption=c["text"], reply_markup=markup)
    else:
        raise ValueError(f"알 수 없는 content_type: {kind}")


class CampaignSender:
    def __init__(self, bot: Bot, db: aiosqlite.Connection, texts: Texts, rate_per_sec: int = 25):
        self.bot = bot
        self.db = db
        self.texts = texts
        self.interval = 1.0 / max(1, rate_per_sec)
        self._tasks: dict[int, asyncio.Task] = {}

    def start(self, campaign_id: int) -> None:
        if campaign_id in self._tasks and not self._tasks[campaign_id].done():
            return
        self._tasks[campaign_id] = asyncio.create_task(self._run(campaign_id))

    async def resume_all(self) -> None:
        """봇이 재시작되면 sending 상태인 캠페인을 이어서 보낸다."""
        for cid in await models.sending_campaign_ids(self.db):
            log.info("캠페인 #%s 이어서 발송", cid)
            self.start(cid)

    async def stop(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def wait(self, campaign_id: int) -> None:
        t = self._tasks.get(campaign_id)
        if t:
            await t

    async def _progress_text(self, c: aiosqlite.Row, counts: dict[str, int]) -> str:
        done = counts["sent"] + counts["blocked"] + counts["failed"]
        return self.texts.get(
            "bc_progress", id=c["id"], done=done, target=c["target_count"],
            sent=counts["sent"], blocked=counts["blocked"], failed=counts["failed"],
        )

    async def _run(self, campaign_id: int) -> None:
        c = await models.get_campaign(self.db, campaign_id)
        if c is None:
            return
        admin = c["created_by"]
        progress_id: int | None = None
        try:
            m = await self.bot.send_message(admin, await self._progress_text(c, await models.recipient_counts(self.db, campaign_id)))
            progress_id = m.message_id
        except TelegramAPIError:
            pass

        last_edit = 0.0
        while True:
            cur = await models.get_campaign(self.db, campaign_id)
            if cur["status"] != "sending":
                return  # 취소/일시정지
            batch = await models.next_pending(self.db, campaign_id)
            if not batch:
                break
            for uid in batch:
                if (await models.get_campaign(self.db, campaign_id))["status"] != "sending":
                    return
                await self._send_one(c, uid)
                await asyncio.sleep(self.interval)
                if progress_id and time.monotonic() - last_edit > PROGRESS_EVERY:
                    last_edit = time.monotonic()
                    await self._edit(admin, progress_id, c, await models.recipient_counts(self.db, campaign_id))

        await models.set_status(self.db, campaign_id, "done")
        counts = await models.recipient_counts(self.db, campaign_id)
        text = self.texts.get("bc_done", id=campaign_id, **counts)
        if progress_id:
            await self._edit(admin, progress_id, c, counts, text=text)
        else:
            try:
                await self.bot.send_message(admin, text)
            except TelegramAPIError:
                pass

    async def _edit(self, chat_id: int, message_id: int, c: aiosqlite.Row, counts: dict[str, int], text: str | None = None) -> None:
        try:
            await self.bot.edit_message_text(text or await self._progress_text(c, counts), chat_id=chat_id, message_id=message_id)
        except TelegramRetryAfter:
            pass  # 진행률 표시는 건너뛰어도 된다
        except TelegramAPIError:
            pass

    async def _send_one(self, c: aiosqlite.Row, uid: int) -> None:
        cid = c["id"]
        while True:
            try:
                await send_campaign_message(self.bot, uid, c)
                await models.mark_recipient(self.db, cid, uid, "sent")
                return
            except TelegramRetryAfter as e:
                log.warning("429: %s초 대기", e.retry_after)
                await asyncio.sleep(e.retry_after + 0.5)
            except TelegramForbiddenError:
                await models.mark_recipient(self.db, cid, uid, "blocked", "forbidden")
                await users.set_active(self.db, uid, False)
                return
            except (TelegramAPIError, ValueError) as e:
                await models.mark_recipient(self.db, cid, uid, "failed", str(e)[:200])
                return
