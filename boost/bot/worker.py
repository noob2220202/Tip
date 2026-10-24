"""백그라운드 작업: 주문 만료 → 입금 조회·매칭 → 패널 접수 → 진행 확인. 한 바퀴를 tick()으로 나눠 테스트한다."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

import aiosqlite

from bot import fulfil, orders, payments
from bot.payments import Event
from bot.smm import SmmPanel
from bot.tron import TronError, TronGrid

log = logging.getLogger(__name__)

ALERT_EVERY = 1800   # 같은 종류의 장애 알림은 30분에 한 번만


class Worker:
    def __init__(self, db: aiosqlite.Connection, tron: TronGrid, panel: SmmPanel,
                 notify: Callable[[Event], Awaitable[None]], alert: Callable[[str], Awaitable[None]],
                 poll_seconds: int = 15) -> None:
        self.db, self.tron, self.panel = db, tron, panel
        self.notify, self.alert = notify, alert
        self.poll_seconds = poll_seconds
        self._last_alert: dict[str, float] = {}

    async def _alert_once(self, key: str, text: str) -> None:
        now = time.time()
        if now - self._last_alert.get(key, 0) < ALERT_EVERY:
            return
        self._last_alert[key] = now
        await self.alert(text)

    async def tick(self, now: int | None = None) -> None:
        now = int(now if now is not None else time.time())
        events: list[Event] = []
        await orders.expire_due(self.db, now)
        since = await orders.open_since(self.db, now)
        if since is not None:
            try:
                transfers = await self.tron.incoming(since * 1000)
            except TronError as e:
                log.warning("입금 조회 실패: %s", e)
                await self._alert_once("tron", f"🚨 TronGrid 입금 조회에 실패하고 있습니다: {e}")
            else:
                events += await payments.process_transfers(self.db, transfers, now)
        events += await fulfil.fulfil_due(self.db, self.panel, now)
        events += await fulfil.poll_status(self.db, self.panel, now)
        for ev in events:
            try:
                await self.notify(ev)
            except Exception:  # noqa: BLE001 - 알림 실패가 다음 작업을 막으면 안 된다
                log.exception("알림 전송 실패: %s", ev)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 어떤 오류든 루프는 계속 돌아야 한다
                log.exception("작업 루프 오류")
            await asyncio.sleep(self.poll_seconds)
