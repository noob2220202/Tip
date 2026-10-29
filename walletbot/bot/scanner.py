"""블록 스캔: 새 블록을 3초마다 받아 등록된 지갑으로 온 입금을 바로 알린다.

지갑마다 조회하는 대신 블록을 받아 우리가 직접 걸러내므로, TronGrid 호출 수가 지갑 수와 상관없이 일정하다.
알림은 입금이 블록에 들어간 직후에 한 번만 보낸다. (실패한 거래는 걸러내고, 확정 여부는 따로 알리지 않는다)
"""
from __future__ import annotations

import logging
import time

import aiosqlite

from bot import store
from bot.chain import TronReader, scan_block
from bot.watcher import Watcher

log = logging.getLogger(__name__)

BLOCK_SECONDS = 3.0
MAX_LAG_SECONDS = 900      # 스캔이 이만큼(약 300블록) 밀리면 블록을 하나씩 따라잡지 않고 내역 조회로 메운 뒤 최신으로 건너뛴다
EMPTY_RETRY = 0.7          # 다음 블록이 아직 없을 때 다시 물어보는 간격(초)


class Scanner:
    def __init__(self, db: aiosqlite.Connection, chain: TronReader, watcher: Watcher, energy=None):
        self.db, self.chain, self.watcher, self.energy = db, chain, watcher, energy

    async def step(self, now: float | None = None) -> float:
        """다음 블록 하나를 처리하고, 다음 호출까지 기다릴 시간(초)을 돌려준다."""
        now = now or time.time()
        scan = await store.get_scan(self.db)
        if scan is None:                         # 처음 켰다: 지금부터 본다
            head = await self.chain.head_block()
            await store.set_scan(self.db, head, int(now * 1000))
            return 0.0
        block = await self.chain.block(scan["last_block"] + 1)
        if block is None:                        # 아직 안 만들어진 블록
            return EMPTY_RETRY
        if now - block.ts_ms / 1000 > MAX_LAG_SECONDS:
            log.warning("블록 스캔이 %d초 밀려서 내역 조회로 보충하고 최신 블록으로 건너뜁니다", now - block.ts_ms / 1000)
            await self.watcher.backfill_all(now)
            head = await self.chain.head_block()
            await store.set_scan(self.db, head, int(now * 1000))
            return 0.0
        watch = await store.watched_hex(self.db)
        pay = self.energy.watch() if self.energy else {}
        if watch or pay:
            found = scan_block(block, {**watch, **pay})
            await self.watcher.notify_deposits(found)          # 결제 주소를 지갑으로 등록한 사람에게도 평소처럼 알린다
            if pay:
                await self.energy.on_deposits([d for d in found if d.to in pay.values()])
        await store.set_scan(self.db, block.number, block.ts_ms)
        # 다음 블록은 이 블록 시각 + 3초쯤에 생긴다. 이미 지났다면(밀렸다면) 기다리지 않고 바로 받는다
        return min(max(0.0, block.ts_ms / 1000 + BLOCK_SECONDS + 0.3 - now), BLOCK_SECONDS + 0.5)
