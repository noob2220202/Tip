"""백그라운드 감시. 입금은 Scanner가 블록에서 바로 잡고, 여기서는 그 뒤처리와 보충을 맡는다.

- 확정 확인: 블록에 들어간 직후 알린 입금이 확정되면 같은 메시지를 '확정'으로 고친다
- 잔액 대조: 주기적으로 잔액을 확인해서, 블록 스캔으로 못 잡은 입금(컨트랙트 경유 등)을 내역 조회로 보충한다
- 놓친 구간 보충: 봇이 오래 꺼져 있었거나 스캔이 많이 밀렸을 때 지갑별 내역 조회로 메운다
- 김프 조건 알림, 정기 시세 알림
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import aiosqlite

from bot import store
from bot import texts as T
from bot.chain import Deposit, RateLimited, TronError, TronReader
from bot.notify import Notifier
from bot.prices import PriceError, Prices

log = logging.getLogger(__name__)

OVERLAP_MS = 15 * 60 * 1000     # 내역 조회는 이만큼 겹쳐서 한다(중복은 notified가 막는다)
RATE_PAUSE = 30                 # 429를 받으면 이 시간(초) 동안 조회를 쉰다
REARM_GAP = 0.2                 # 김프가 조건에서 이만큼(%p) 되돌아가야 다시 알릴 수 있다
DAILY_WINDOW = timedelta(minutes=10)   # 정기 시세는 정한 시각에서 이 시간 안이면 늦게라도 보낸다(재시작 직후 등)
ERROR_ALERT_EVERY = 1800        # 같은 종류의 오류를 관리자에게 알리는 최소 간격(초)
RECONCILE_BATCH = 20            # 한 번에 잔액을 대조하는 주소 수 (호출이 몰리지 않게 나눈다)
CONFIRM_RETRY = 20              # 확정 확인 간격(초)
CONFIRM_DROP_CHECK = 600        # 이 시간(초)이 지나도 확정이 안 되면 거래가 아직 있는지 본다
CONFIRM_GIVE_UP = 1800          # 이 시간(초)이 지나면 확인을 그만둔다


class Watcher:
    def __init__(self, db: aiosqlite.Connection, chain: TronReader, prices: Prices, notifier: Notifier,
                 reconcile_seconds: int = 1800):
        self.db, self.chain, self.prices, self.notifier = db, chain, prices, notifier
        self.reconcile_seconds = reconcile_seconds
        self._pause_until = 0.0
        self._last_alert: dict[str, float] = {}

    async def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        await self.confirm(now)
        await self.reconcile(now)
        await self.kimp()
        await self.daily(now)

    async def alert_admins(self, key: str, text: str, now: float) -> None:
        if now - self._last_alert.get(key, 0) >= ERROR_ALERT_EVERY:
            self._last_alert[key] = now
            await self.notifier.admins(text)

    def paused(self, now: float) -> bool:
        return now < self._pause_until

    async def rate_limited(self, now: float) -> None:
        self._pause_until = now + RATE_PAUSE
        await self.alert_admins("rate", "⚠️ TronGrid 호출 한도를 넘었습니다. 잠시 조회를 쉽니다. "
                                        "API 키나 MAX_WALLETS_TOTAL, RECONCILE_MINUTES를 확인해 주십시오.", now)

    # ---- 입금 알림 ----

    async def notify_deposits(self, found: list[Deposit], pending: bool, now: float | None = None) -> None:
        """받은 지갑(Deposit.to)의 주인들에게 알린다. pending이면 확정 대기로 보내고 확정 확인 목록에 올린다."""
        if not found:
            return
        now = now or time.time()
        try:
            quote = await self.prices.get()
        except PriceError:
            quote = None                   # 시세를 못 가져와도 입금 알림은 보낸다 (원화 환산만 뺀다)
        for d in found:
            for w in await store.wallets_at(self.db, d.to):
                if d.ts_ms < w["created_at"] * 1000:
                    continue                # 등록 전 입금은 알리지 않는다
                if not await store.claim_tx(self.db, w["id"], d.txid, d.token):
                    continue
                s = await store.get_settings(self.db, w["user_id"])
                if d.value < (s["min_usdt"] if d.token == "USDT" else s["min_trx"]):
                    await store.count_filtered(self.db, w["user_id"])
                    continue
                text = T.deposit_text(w, d, quote, "pending" if pending else "confirmed")
                mid = await self.notifier.send_id(w["user_id"], text)
                if mid is None:
                    # 막힌 사용자가 아니라 일시 오류라면 다음에 다시 시도하도록 기록을 지운다
                    async with self.db.execute("SELECT is_active FROM users WHERE user_id=?", (w["user_id"],)) as cur:
                        u = await cur.fetchone()
                    if u and u["is_active"]:
                        await store.unclaim_tx(self.db, w["id"], d.txid, d.token)
                elif pending:
                    await store.add_pending(self.db, w["id"], w["user_id"], mid, d, int(now))

    # ---- 확정 확인 ----

    async def confirm(self, now: float) -> None:
        if self.paused(now):
            return
        for row in await store.due_pending(self.db, int(now)):
            age = now - row["first_seen"]
            d = Deposit(row["txid"], row["token"], row["sender"], row["value"], row["ts_ms"])
            try:
                if await self.chain.solidified(row["txid"]):
                    status = "confirmed"
                elif age >= CONFIRM_DROP_CHECK and not await self.chain.tx_exists(row["txid"]):
                    status = "dropped"
                elif age >= CONFIRM_GIVE_UP:
                    status = "unknown"
                else:
                    await store.delay_pending(self.db, row, int(now) + CONFIRM_RETRY)
                    continue
            except RateLimited:
                await self.rate_limited(now)
                return
            except TronError as e:
                log.warning("확정 확인 실패 %s: %s", row["txid"], e)
                await store.delay_pending(self.db, row, int(now) + CONFIRM_RETRY)
                continue
            async with self.db.execute("SELECT * FROM wallets WHERE id=?", (row["wallet_id"],)) as cur:
                w = await cur.fetchone()
            if w:
                try:
                    quote = await self.prices.get()
                except PriceError:
                    quote = None
                await self.notifier.edit(row["user_id"], row["message_id"], T.deposit_text(w, d, quote, status))
            await store.delete_pending(self.db, row)

    # ---- 잔액 대조 · 놓친 입금 보충 ----

    async def backfill_address(self, address: str, now: float) -> None:
        """내역 조회로 이 주소의 놓친 입금을 알린다(확정된 것만). 실패하면 예외를 그대로 올린다."""
        row = next((r for r in await store.watched_addresses(self.db) if r["address"] == address), None)
        if row is None:
            return
        started_ms = int(now * 1000)
        found = await self.chain.deposits(address, max(0, row["cursor_ms"] - OVERLAP_MS))
        for d in found:
            d = Deposit(d.txid, d.token, d.sender, d.value, d.ts_ms, address)
            await self.notify_deposits([d], pending=False, now=now)
        await store.set_cursor(self.db, address, started_ms)

    async def backfill_all(self, now: float) -> None:
        """오래 꺼져 있었거나 스캔이 많이 밀린 뒤, 모든 주소의 놓친 입금을 내역 조회로 메운다."""
        for row in await store.watched_addresses(self.db):
            try:
                await self.backfill_address(row["address"], now)
            except RateLimited:
                await self.rate_limited(now)
                return
            except TronError as e:
                log.warning("놓친 입금 보충 실패 %s: %s", row["address"], e)

    async def reconcile(self, now: float) -> None:
        """잔액을 대조해서 늘어난 주소만 내역을 조회한다. 블록 스캔이 못 잡는 입금(컨트랙트 경유)을 늦게라도 알린다."""
        if self.paused(now):
            return
        for row in await store.due_balance(self.db, int(now) - self.reconcile_seconds, RECONCILE_BATCH):
            address = row["address"]
            try:
                bal = await self.chain.balance(address, now, fresh=True)
                grew = row["usdt"] is not None and (bal.usdt > row["usdt"] or bal.trx > row["trx"])
                if grew:
                    await self.backfill_address(address, now)
            except RateLimited:
                await self.rate_limited(now)
                return
            except TronError as e:
                log.warning("잔액 대조 실패 %s: %s", address, e)
                await self.alert_admins("chain", f"⚠️ 잔액 대조가 실패하고 있습니다: {e}", now)
                continue
            await store.set_balance(self.db, address, bal.usdt, bal.trx, int(now))

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
