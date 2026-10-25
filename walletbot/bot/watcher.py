"""백그라운드 감시: 입금 확인, 김프 조건, 정기 시세. tick() 하나가 한 번 돌 때 하는 일이다."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import aiosqlite

from bot import store
from bot import texts as T
from bot.chain import RateLimited, TronError, TronReader
from bot.notify import Notifier
from bot.prices import PriceError, Prices

log = logging.getLogger(__name__)

OVERLAP_MS = 15 * 60 * 1000     # 확정이 늦게 되는 거래를 놓치지 않으려고 이만큼 겹쳐 조회한다(중복은 notified가 막는다)
RATE_PAUSE = 30                 # 429를 받으면 이 시간(초) 동안 조회를 쉰다
REARM_GAP = 0.2                 # 김프가 조건에서 이만큼(%p) 되돌아가야 다시 알릴 수 있다
DAILY_WINDOW = timedelta(minutes=10)   # 정기 시세는 정한 시각에서 이 시간 안이면 늦게라도 보낸다(재시작 직후 등)
ERROR_ALERT_EVERY = 1800        # 같은 종류의 오류를 관리자에게 알리는 최소 간격(초)


class Watcher:
    def __init__(self, db: aiosqlite.Connection, chain: TronReader, prices: Prices, notifier: Notifier,
                 poll_seconds: int = 60):
        self.db, self.chain, self.prices, self.notifier, self.poll = db, chain, prices, notifier, poll_seconds
        self._last_poll: dict[str, float] = {}
        self._pause_until = 0.0
        self._last_alert: dict[str, float] = {}

    async def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        await self.deposits(now)
        await self.kimp()
        await self.daily(now)

    async def _alert_admins(self, key: str, text: str, now: float) -> None:
        if now - self._last_alert.get(key, 0) >= ERROR_ALERT_EVERY:
            self._last_alert[key] = now
            await self.notifier.admins(text)

    # ---- 입금 ----

    async def deposits(self, now: float) -> None:
        if now < self._pause_until:
            return
        for row in await store.watched_addresses(self.db):
            address = row["address"]
            if now - self._last_poll.get(address, 0) < self.poll:
                continue
            self._last_poll[address] = now
            started_ms = int(now * 1000)
            try:
                found = await self.chain.deposits(address, max(0, row["cursor_ms"] - OVERLAP_MS))
            except RateLimited:
                self._pause_until = now + RATE_PAUSE
                await self._alert_admins("rate", "⚠️ TronGrid 호출 한도를 넘었습니다. 잠시 조회를 쉽니다. "
                                                 "API 키나 MAX_WALLETS_TOTAL, POLL_SECONDS를 확인해 주십시오.", now)
                return
            except TronError as e:
                log.warning("입금 조회 실패 %s: %s", address, e)
                await self._alert_admins("chain", f"⚠️ 입금 조회가 실패하고 있습니다: {e}", now)
                continue
            await self._notify(address, found)
            await store.set_cursor(self.db, address, started_ms)

    async def _notify(self, address: str, found) -> None:
        if not found:
            return
        try:
            quote = await self.prices.get()
        except PriceError:
            quote = None                   # 시세를 못 가져와도 입금 알림은 보낸다 (원화 환산만 뺀다)
        for w in await store.wallets_at(self.db, address):
            s = await store.get_settings(self.db, w["user_id"])
            for d in found:
                if d.ts_ms < w["created_at"] * 1000:
                    continue                # 등록 전 입금은 알리지 않는다
                if not await store.claim_tx(self.db, w["id"], d.txid, d.token):
                    continue
                if d.value < (s["min_usdt"] if d.token == "USDT" else s["min_trx"]):
                    await store.count_filtered(self.db, w["user_id"])
                    continue
                if not await self.notifier.send(w["user_id"], T.deposit_text(w, d, quote)):
                    # 막힌 사용자가 아니라 일시 오류라면 다음 조회 때 다시 시도하도록 기록을 지운다
                    async with self.db.execute("SELECT is_active FROM users WHERE user_id=?", (w["user_id"],)) as cur:
                        u = await cur.fetchone()
                    if u and u["is_active"]:
                        await store.unclaim_tx(self.db, w["id"], d.txid, d.token)

    # ---- 김프 조건 ----

    async def kimp(self) -> None:
        rules = await store.active_rules(self.db)
        if not rules:
            return
        try:
            q = await self.prices.get()
        except PriceError:
            return
        k = q.kimp
        if k is None or q.stale:
            return
        for r in rules:
            hit = k >= r["pct"] if r["direction"] == "up" else k <= r["pct"]
            back = k <= r["pct"] - REARM_GAP if r["direction"] == "up" else k >= r["pct"] + REARM_GAP
            if r["armed"] and hit:
                await store.set_armed(self.db, r["id"], False)
                await self.notifier.send(r["user_id"], T.rule_alert(r, q))
            elif not r["armed"] and back:
                await store.set_armed(self.db, r["id"], True)

    # ---- 정기 시세 ----

    async def daily(self, now: float) -> None:
        due_rows = await store.due_daily(self.db)
        if not due_rows:
            return
        at = datetime.fromtimestamp(now, T.KST)
        today = at.strftime("%Y-%m-%d")
        quote = None
        for row in due_rows:
            hh, mm = map(int, row["hhmm"].split(":"))
            scheduled = at.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if not (scheduled <= at < scheduled + DAILY_WINDOW):
                continue
            if quote is None:
                try:
                    quote = await self.prices.get()
                except PriceError:
                    return                  # 다음 틱에 다시 시도한다 (아직 보냈다고 표시하지 않았다)
            if await store.mark_daily_sent(self.db, row["user_id"], row["hhmm"], today):
                await self.notifier.send(row["user_id"], T.daily_text(quote))
