"""이벤트 실행. 한 명씩 구독을 확인하고 기프트콘을 보낸다.

코드는 한 번만 쓸 수 있으므로 두 사람에게 같은 코드가 가면 안 된다.
- 보내기 전에 DB에서 코드를 먼저 '배정'한다.
- 확실히 못 보낸 경우(차단 등)에만 코드를 되돌린다.
- 보내졌는지 알 수 없는 경우(네트워크 오류 등)는 되돌리지 않고 '확인 필요'로 남겨 관리자에게 알린다.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter)

from bot import store
from bot import texts as T
from bot.eligibility import ERROR, MISSING, Checker
from core import users

log = logging.getLogger(__name__)

MAX_CHECK_ERRORS = 5       # 구독 확인이 이만큼 연달아 실패하면 일시 중지한다(검수 서비스 장애 등)
PROGRESS_EVERY = 15        # 진행 상황을 관리자에게 보여주는 간격(초)
BATCH = 20                 # 전원 발송에서 한 번에 가져오는 후보 수


class Engine:
    def __init__(self, bot: Bot, db: aiosqlite.Connection, checker: Checker, admin_ids, per_sec: float = 20):
        self.bot, self.db, self.checker, self.admin_ids = bot, db, checker, frozenset(admin_ids)
        self.gap = 1.0 / max(1.0, per_sec)
        self.tasks: dict[int, asyncio.Task] = {}
        self._progress: dict[int, tuple[int, float]] = {}      # 이벤트 id -> (진행 메시지 id, 마지막 갱신 시각)

    # ---- 시작 · 정지 ----

    def start(self, event_id: int) -> None:
        t = self.tasks.get(event_id)
        if t is None or t.done():
            self.tasks[event_id] = asyncio.create_task(self.run(event_id), name=f"giveaway-{event_id}")

    async def resume_running(self) -> None:
        """재시작 뒤: 보내던 도중이던 건을 '확인 필요'로 돌리고, 진행 중이던 이벤트를 이어서 한다."""
        await store.recover(self.db)
        for ev in await store.running_events(self.db):
            self.start(ev["id"])

    async def stop_all(self) -> None:
        for t in self.tasks.values():
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def _say(self, ev, text: str) -> None:
        try:
            await self.bot.send_message(ev["created_by"], text)
        except TelegramAPIError as e:
            log.warning("관리자 알림 실패: %s", e)

    async def _pause(self, ev, reason: str) -> None:
        if await store.transition(self.db, ev["id"], ("running",), "paused", reason):
            await self._say(ev, T.paused_text(ev, reason))

    # ---- 실행 ----

    async def run(self, event_id: int) -> None:
        ev = await store.get_event(self.db, event_id)
        if ev is None or ev["status"] != "running":
            return
        draw = ev["kind"] == "draw"
        errors = 0
        while True:
            ev = await store.get_event(self.db, event_id)
            if ev["status"] != "running":
                return                                          # 취소·일시 중지됨
            tries = await store.try_counts(self.db, event_id)
            if draw and (tries["won"] >= ev["winners"] or (await store.item_counts(self.db, event_id))["free"] == 0):
                break
            cands = await store.candidates(self.db, event_id, self.admin_ids, randomize=draw, limit=1 if draw else BATCH)
            if not cands:
                break
            for uid in cands:
                outcome = await self._one(ev, uid)
                if outcome == ERROR:
                    errors += 1
                    if errors >= MAX_CHECK_ERRORS:
                        await self._pause(ev, "채널 구독 확인이 계속 실패합니다. 검수 봇(verify)이 켜져 있고 채널 관리자인지 확인해 주십시오.")
                        return
                    await asyncio.sleep(0.2)
                    continue
                errors = 0
                if outcome == "pause":
                    return
                if outcome == "stop":
                    break
                await self._report(ev)
                await asyncio.sleep(self.gap)
                if draw and (await store.try_counts(self.db, event_id))["won"] >= ev["winners"]:
                    break
        await self._finish(ev)

    async def _one(self, ev, uid: int) -> str:
        """한 명을 처리한다. 반환: won | unsub | blocked | uncertain | error(구독 확인 실패) | pause | stop(보낼 코드 없음)"""
        state = await self.checker.check(uid)
        if state == ERROR:
            return ERROR
        if state == MISSING:
            await store.set_try(self.db, ev["id"], uid, "unsub")
            return "unsub"
        draw = ev["kind"] == "draw"
        item = await store.claim_item(self.db, ev["id"], uid) if draw else await store.first_item(self.db, ev["id"])
        if item is None:
            return "stop"
        await store.set_try(self.db, ev["id"], uid, "sending", item["id"])    # 보내기 전에 기록한다 (재시작 때 불확실로 돌리려고)
        result, detail = await self._send(ev, item, uid)

        if result == "sent":
            if draw:
                await store.set_item(self.db, item["id"], "sent")
            await store.set_try(self.db, ev["id"], uid, "won", item["id"])
            return "won"
        if result == "blocked":
            if draw:
                await store.release_item(self.db, item["id"])
            await store.set_try(self.db, ev["id"], uid, "blocked", item["id"])
            await users.set_active(self.db, uid, False)
            return "blocked"
        if result == "uncertain":
            if draw:
                await store.set_item(self.db, item["id"], "uncertain")
            await store.set_try(self.db, ev["id"], uid, "uncertain", item["id"])
            return "uncertain"
        # 보낼 수 없는 내용(잘못된 이미지 등)이나 요청 제한: 코드를 되돌리고 이 사람은 시도하지 않은 것으로 하고 멈춘다
        if draw:
            await store.release_item(self.db, item["id"])
        await store.delete_try(self.db, ev["id"], uid)
        await self._pause(ev, detail)
        return "pause"

    async def _send(self, ev, item, uid: int) -> tuple[str, str]:
        text = T.delivery(ev, item["kind"], item["content"])
        for _ in range(3):
            try:
                if item["kind"] == "photo":
                    await self.bot.send_photo(uid, photo=item["content"], caption=text)
                else:
                    await self.bot.send_message(uid, text)
                return "sent", ""
            except TelegramRetryAfter as e:                     # 요청 제한: 보내지지 않았다는 게 확실하니 기다렸다 다시 보낸다
                await asyncio.sleep(min(e.retry_after, 30))
            except TelegramForbiddenError:
                return "blocked", ""
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "chat not found" in msg or "deactivated" in msg:
                    return "blocked", ""
                return "invalid", f"기프트콘을 보낼 수 없습니다: {e.message}. 등록한 이미지·코드를 확인해 주십시오."
            except TelegramAPIError as e:                       # 네트워크 오류·서버 오류: 보내졌는지 알 수 없다
                log.warning("기프트콘 전송 결과 불확실 user=%s: %s", uid, e)
                return "uncertain", ""
        return "invalid", "텔레그램의 요청 제한이 풀리지 않아 멈췄습니다. 잠시 뒤 이어서 실행해 주십시오."

    # ---- 보고 ----

    async def _report(self, ev) -> None:
        now = time.monotonic()
        mid, last = self._progress.get(ev["id"], (0, 0.0))
        if now - last < PROGRESS_EVERY:
            return
        tries = await store.try_counts(self.db, ev["id"])
        target = f" / {ev['winners']}" if ev["kind"] == "draw" else ""
        text = T.progress_text(ev, tries, target)
        try:
            if mid:
                await self.bot.edit_message_text(text, chat_id=ev["created_by"], message_id=mid)
            else:
                mid = (await self.bot.send_message(ev["created_by"], text)).message_id
        except TelegramAPIError:
            pass
        self._progress[ev["id"]] = (mid, now)

    async def _finish(self, ev) -> None:
        if not await store.transition(self.db, ev["id"], ("running",), "done"):
            return
        tries = await store.try_counts(self.db, ev["id"])
        items = await store.item_counts(self.db, ev["id"])
        note = ""
        if ev["kind"] == "draw" and tries["won"] < ev["winners"]:
            note = f"대상자 또는 기프트콘이 부족해 {ev['winners']}명 중 {tries['won']}명에게만 보냈습니다."
            await self.db.execute("UPDATE events SET stop_reason=? WHERE id=?", (note, ev["id"]))
            await self.db.commit()
        ev = await store.get_event(self.db, ev["id"])
        out = "🏁 <b>이벤트가 끝났습니다.</b>\n\n" + T.event_text(ev, items, tries=tries) + T.winners_text(await store.winners_of(self.db, ev["id"]))
        if tries["uncertain"]:
            out += "\n\n⚠️ <b>확인 필요</b> — 전송 결과를 알 수 없는 건이 있습니다. 해당 코드는 다른 분에게 다시 보내지 않았습니다. 직접 확인해 주십시오."
        await self._say(ev, out)
