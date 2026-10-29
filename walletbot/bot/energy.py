"""에너지 임대: 주문 → TRX 입금 확인 → 공급처에 주문 → 체인에서 에너지 도착 확인.

가격: 공급처 세 곳의 1시간 원가 중 가장 싼 값 + 마진(기본 0)을 0.001 TRX 단위로 올린 값.
입금 구분: 주문마다 금액 끝자리를 다르게 해서(0.001 TRX 단위) 금액만으로 주문을 찾는다. 메모는 쓰지 않는다(1 TRX 수수료).
  보내는 주소를 묻지 않으므로 거래소에서 보내도 된다. 에너지는 주문할 때 입력한 주소로 간다.
중복 구매 방지: paid → ordering으로 먼저 바꾼 뒤에만 공급처를 부른다. 응답이 불확실하면 다른 공급처로 넘기지 않고
  같은 공급처에 같은 주문 번호로만 다시 묻는다(공급처가 같은 번호의 두 번째 요청을 새 주문으로 만들지 않는다).
서버에는 개인키가 없다. 환불이 필요한 경우(금액 불일치, 모든 공급처 실패)는 관리자에게 알려 수동으로 처리한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from dataclasses import dataclass

import aiosqlite

from bot import energy_texts as E
from bot.chain import Deposit, RateLimited, TronError, TronReader
from bot.notify import Notifier
from bot.suppliers import Rejected, Supplier, SupplierError, Uncertain
from core.tron import address_to_hex, hex_to_address

log = logging.getLogger(__name__)

SCHEMA = """
-- 에너지 임대 주문. amount는 받을 TRX(sun), cost는 견적 때 원가, paid_cost는 공급처에서 실제로 빠진 금액
CREATE TABLE IF NOT EXISTS energy_orders (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL,
  receiver      TEXT NOT NULL,
  need          INTEGER NOT NULL,
  energy        INTEGER NOT NULL,
  amount        INTEGER NOT NULL,
  cost          INTEGER NOT NULL,
  status        TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL,
  txid          TEXT,
  payer         TEXT NOT NULL DEFAULT '',
  paid_at       INTEGER,
  supplier      TEXT NOT NULL DEFAULT '',
  supplier_ref  TEXT NOT NULL DEFAULT '',
  delegate_tx   TEXT NOT NULL DEFAULT '',
  paid_cost     INTEGER NOT NULL DEFAULT 0,
  tried         TEXT NOT NULL DEFAULT '',
  attempts      INTEGER NOT NULL DEFAULT 0,
  next_try      INTEGER NOT NULL DEFAULT 0,
  delivered_at  INTEGER,
  done_at       INTEGER,
  note          TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_eo_txid ON energy_orders(txid) WHERE txid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_eo_status ON energy_orders(status);
CREATE INDEX IF NOT EXISTS idx_eo_user ON energy_orders(user_id, id);

-- 결제 주소로 들어온 TRX. 같은 입금을 두 번 처리하지 않는다
CREATE TABLE IF NOT EXISTS energy_payments (
  txid      TEXT PRIMARY KEY,
  sender    TEXT NOT NULL,
  value     INTEGER NOT NULL,
  ts_ms     INTEGER NOT NULL,
  kind      TEXT NOT NULL,
  order_id  INTEGER,
  seen_at   INTEGER NOT NULL
);
"""

SUN = 1_000_000
STEP = 1_000                 # 금액 단위 0.001 TRX. 같은 원가의 주문끼리는 이 단위로 끝자리를 다르게 한다
SLOTS = 100                  # 끝자리 후보 수 (최대 +0.099 TRX)
BUFFER = 500                 # 모의 실행 값에 더하는 여유 에너지
MAX_ENERGY = 300_000         # 한 번에 주문할 수 있는 최대 에너지 (USDT 한 번 전송은 많아야 13만)
LATE_SECONDS = 1800          # 만료 뒤 이 시간 안에 들어온 정확한 금액은 그대로 처리한다(거래소 출금 지연 등)
DUST = 500_000               # 0.5 TRX 미만 입금은 스팸으로 보고 무시한다
CATCHUP_EVERY = 30           # 열린 주문이 있을 때 결제 주소 내역을 다시 훑는 간격(초). 블록 스캔이 놓친 입금 보충
UNCERTAIN_RETRY = 20         # 불확실한 응답 뒤 같은 공급처에 다시 묻는 간격(초)
UNCERTAIN_MAX = 6            # 이만큼 불확실하면 사람이 확인한다
VERIFY_SECONDS = 120         # 공급처 성공 뒤 체인에서 에너지가 보일 때까지 기다리는 시간
BALANCE_EVERY = 600          # 공급처 잔고 확인 간격(초)
ALERT_EVERY = 1800
USER_ORDERS_PER_HOUR = 10    # 결제 없이 주문만 반복해 끝자리를 다 쓰지 못하게

# 받는 쪽 USDT 보유 여부만 알 때 모의 실행에 쓰는 주소. HOLDER는 USDT가 많은 주소, EMPTY는 아무도 쓸 수 없는 빈 주소
SIM_OWNER = "TV6MuMXfmLbBqPZvBHdwFsDnQeVfnmiuSi"
SIM_HOLDER = "TNXoiAJ3dct8Fjg4M9fkLFh9S2v9TXc32G"
SIM_EMPTY = hex_to_address("41" + hashlib.sha256(b"walletbot energy empty receiver").hexdigest()[:40])
# 모의 실행이 모두 실패했을 때 쓰는 값 (2026-10 기준 USDT 전송 실측: 받는 쪽에 USDT 있음 64,285 / 없음 130,285)
FALLBACK_NEED = {True: 64_285, False: 130_285}

OPEN = ("awaiting",)
WORKING = ("paid", "ordering", "delivered")


def order_energy(need: int, minimum: int) -> int:
    return max(minimum, math.ceil((need + BUFFER) / 1000) * 1000)


@dataclass(frozen=True)
class Quote:
    energy: int
    cost: int                 # 가장 싼 공급처 원가(sun)
    price: int                # 손님이 낼 금액의 기준(sun, 끝자리 배정 전)
    supplier: str
    burn: int                 # 에너지 없이 보내면 태우는 TRX(sun)


class Desk:
    def __init__(self, db: aiosqlite.Connection, chain: TronReader, notifier: Notifier, suppliers: list[Supplier],
                 pay_address: str, margin: int = 0, max_loss: int = SUN, ttl: int = 600,
                 low_balance: int = 50 * SUN) -> None:
        self.db, self.chain, self.notifier = db, chain, notifier
        self.suppliers = {s.name: s for s in suppliers}
        self.pay_address, self.pay_hex = pay_address, address_to_hex(pay_address) if pay_address else None
        self.margin, self.max_loss, self.ttl, self.low_balance = margin, max_loss, ttl, low_balance
        self.wake = asyncio.Event()
        self._create_lock = asyncio.Lock()
        self._last_catchup = 0.0
        self._last_balance = 0.0
        self._last_alert: dict[str, float] = {}
        self.balances: dict[str, int | None] = {}
        self._menu: tuple[float, dict] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.pay_hex and self.suppliers)

    def watch(self) -> dict[str, str]:
        """블록 스캔이 결제 주소로 온 입금도 찾도록 {hex: 주소}."""
        return {self.pay_hex: self.pay_address} if self.enabled else {}

    async def close(self) -> None:
        for s in self.suppliers.values():
            await s.close()

    async def alert(self, key: str, text: str, now: float | None = None, every: int = ALERT_EVERY) -> None:
        now = now or time.time()
        if now - self._last_alert.get(key, 0) >= every:
            self._last_alert[key] = now
            await self.notifier.admins(text)

    # ───────────── 견적 ─────────────

    async def need_for(self, sender: str, recipient: str | None = None, has_usdt: bool | None = None) -> int:
        """sender가 USDT를 한 번 보내는 데 필요한 에너지. 받는 주소를 알면 그 주소로, 모르면 보유 여부로 모의 실행한다."""
        target = recipient or (SIM_HOLDER if has_usdt else SIM_EMPTY)
        for owner in (sender, SIM_OWNER):
            if owner == target:
                continue
            try:
                sim = await self.chain.simulate_usdt(owner, target)
            except TronError as e:
                log.info("모의 실행 실패 %s→%s: %s", owner, target, e)
                continue
            if sim.ok and sim.energy > 0:
                return sim.energy
        if recipient:
            try:
                has_usdt = await self.chain.usdt_of(recipient) > 0
            except TronError:
                has_usdt = False
        return FALLBACK_NEED[bool(has_usdt)]

    async def _costs(self, energy: int) -> list[tuple[int, Supplier]]:
        """공급처별 원가, 싼 순서. 가격을 못 가져온 곳은 뺀다."""
        async def one(s: Supplier):
            if energy < s.min_energy:
                return None
            try:
                return await s.cost(energy), s
            except SupplierError as e:
                log.warning("%s 가격 조회 실패: %s", s.name, e)
                return None

        found = [r for r in await asyncio.gather(*(one(s) for s in self.suppliers.values())) if r]
        return sorted(found, key=lambda r: r[0])

    async def quote(self, need: int) -> Quote | None:
        minimum = min(s.min_energy for s in self.suppliers.values())
        energy = order_energy(need, max(minimum, 65_000))
        costs = await self._costs(energy)
        if not costs:
            return None
        cost, s = costs[0]
        try:
            fee = await self.chain.energy_fee()
        except TronError:
            fee = 100
        return Quote(energy, cost, math.ceil((cost + self.margin) / STEP) * STEP, s.name, need * fee)

    async def menu_prices(self, now: float | None = None) -> dict[bool, tuple[int, int]]:
        """소개 화면 가격: {받는 쪽 USDT 보유 여부: (가격, 태우면 드는 TRX)}. 1분 기억한다."""
        now = now or time.time()
        if self._menu and now - self._menu[0] < 60:
            return self._menu[1]
        out = {}
        for has in (True, False):
            q = await self.quote(await self.need_for(SIM_OWNER, has_usdt=has))
            if q:
                out[has] = (q.price, q.burn)
        if out:
            self._menu = (now, out)
        return out

    # ───────────── 주문 ─────────────

    async def open_order(self, user_id: int) -> aiosqlite.Row | None:
        async with self.db.execute("SELECT * FROM energy_orders WHERE user_id=? AND status IN ('awaiting') "
                                   "ORDER BY id DESC LIMIT 1", (user_id,)) as cur:
            return await cur.fetchone()

    async def orders_of(self, user_id: int, limit: int = 5) -> list[aiosqlite.Row]:
        async with self.db.execute("SELECT * FROM energy_orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
                                   (user_id, limit)) as cur:
            return list(await cur.fetchall())

    async def get(self, order_id: int) -> aiosqlite.Row | None:
        async with self.db.execute("SELECT * FROM energy_orders WHERE id=?", (order_id,)) as cur:
            return await cur.fetchone()

    async def recent_count(self, user_id: int, now: float) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM energy_orders WHERE user_id=? AND created_at>=?",
                                   (user_id, int(now) - 3600)) as cur:
            return (await cur.fetchone())[0]

    async def create(self, user_id: int, receiver: str, need: int, q: Quote, now: float | None = None) -> aiosqlite.Row | None:
        """주문을 만든다. 끝자리를 배정할 자리가 없으면 None."""
        now = int(now or time.time())
        async with self._create_lock:
            async with self.db.execute(
                "SELECT amount FROM energy_orders WHERE status='awaiting' OR "
                "(status IN ('expired','cancelled') AND txid IS NULL AND expires_at>=?)",
                (now - LATE_SECONDS,),
            ) as cur:
                used = {r[0] for r in await cur.fetchall()}
            amount = next((q.price + k * STEP for k in range(SLOTS) if q.price + k * STEP not in used), None)
            if amount is None:
                return None
            await self.db.execute("UPDATE energy_orders SET status='cancelled' WHERE user_id=? AND status='awaiting'",
                                  (user_id,))
            cur = await self.db.execute(
                "INSERT INTO energy_orders(user_id, receiver, need, energy, amount, cost, status, created_at, expires_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (user_id, receiver, need, q.energy, amount, q.cost, "awaiting", now, now + self.ttl),
            )
            await self.db.commit()
        return await self.get(cur.lastrowid)

    async def cancel(self, order_id: int, user_id: int) -> bool:
        cur = await self.db.execute("UPDATE energy_orders SET status='cancelled' WHERE id=? AND user_id=? "
                                    "AND status='awaiting'", (order_id, user_id))
        await self.db.commit()
        return cur.rowcount == 1

    async def _move(self, order_id: int, frm: tuple[str, ...], to: str, **fields) -> bool:
        sets = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
        marks = ",".join("?" * len(frm))
        cur = await self.db.execute(f"UPDATE energy_orders SET {sets} WHERE id=? AND status IN ({marks})",
                                    (to, *fields.values(), order_id, *frm))
        await self.db.commit()
        return cur.rowcount == 1

    # ───────────── 입금 ─────────────

    async def on_deposits(self, found: list[Deposit], now: float | None = None) -> None:
        """결제 주소로 온 TRX를 주문과 맞춘다. 블록 스캔과 내역 보충이 같이 부른다."""
        now = int(now or time.time())
        hit = False
        for d in sorted(found, key=lambda x: x.ts_ms):
            if d.to != self.pay_address:
                continue
            try:
                await self.db.execute(
                    "INSERT INTO energy_payments(txid, sender, value, ts_ms, kind, seen_at) VALUES(?,?,?,?,?,?)",
                    (d.txid, d.sender, d.value, d.ts_ms, "new", now))
            except aiosqlite.IntegrityError:
                continue
            kind, order = await self._match(d, now)
            await self.db.execute("UPDATE energy_payments SET kind=?, order_id=? WHERE txid=?",
                                  (kind, order["id"] if order else None, d.txid))
            await self.db.commit()
            if kind == "matched":
                hit = True
                await self.notifier.send(order["user_id"], E.paid(order))
            elif kind == "usdt":
                await self.alert(f"pay:{d.txid}", E.admin_unknown(d, "USDT로 입금됨 — 환불 필요"), now, 0)
            elif kind == "mismatch":
                await self.notifier.send(order["user_id"], E.mismatch(order, d.value))
                await self.alert(f"pay:{d.txid}", E.admin_unknown(d, f"금액 불일치 — 주문 #{order['id']}"), now, 0)
            elif kind == "unknown":
                await self.alert(f"pay:{d.txid}", E.admin_unknown(d, "맞는 주문 없음"), now, 0)
        if hit:
            self.wake.set()

    async def _match(self, d: Deposit, now: int) -> tuple[str, aiosqlite.Row | None]:
        if d.token != "TRX":
            return ("usdt", None) if d.value >= SUN else ("dust", None)
        async with self.db.execute(
            "SELECT * FROM energy_orders WHERE amount=? AND txid IS NULL AND created_at*1000<=? AND "
            "(status='awaiting' OR (status IN ('expired','cancelled') AND expires_at*1000>=?)) ORDER BY id DESC",
            (d.value, d.ts_ms + 60_000, d.ts_ms - LATE_SECONDS * 1000),
        ) as cur:
            cands = list(await cur.fetchall())
        if cands:
            o = cands[0]
            if await self._move(o["id"], ("awaiting", "expired", "cancelled"), "paid", txid=d.txid, payer=d.sender,
                                paid_at=now, next_try=0):
                return "matched", await self.get(o["id"])
        if d.value < DUST:
            return "dust", None
        # 금액이 다르지만 보낸 주소가 주문한 지갑이면 그 주문의 손님에게 알린다
        async with self.db.execute(
            "SELECT * FROM energy_orders WHERE receiver=? AND status IN ('awaiting','expired','cancelled') "
            "AND expires_at*1000>=? ORDER BY id DESC LIMIT 1", (d.sender, d.ts_ms - LATE_SECONDS * 1000),
        ) as cur:
            o = await cur.fetchone()
        return ("mismatch", o) if o else ("unknown", None)

    async def catch_up(self, now: float) -> None:
        """블록 스캔이 놓친 입금(봇이 꺼져 있었거나 스캔이 밀린 경우)을 결제 주소 내역으로 보충한다."""
        async with self.db.execute("SELECT MIN(created_at) FROM energy_orders WHERE status='awaiting' OR "
                                   "(status IN ('expired','cancelled') AND expires_at>=?)", (int(now) - LATE_SECONDS,)) as cur:
            since = (await cur.fetchone())[0]
        if since is None:
            return
        try:
            found = await self.chain.deposits(self.pay_address, since * 1000 - 60_000)
        except RateLimited:
            return
        except TronError as e:
            log.warning("결제 주소 내역 조회 실패: %s", e)
            return
        await self.on_deposits([Deposit(d.txid, d.token, d.sender, d.value, d.ts_ms, self.pay_address) for d in found], now)

    # ───────────── 처리 루프 ─────────────

    async def recover(self) -> None:
        """재시작 직후: 공급처를 부르던 중이던 주문은 같은 번호로 다시 묻도록 대기열에 둔다(새 주문을 만들지 않는다)."""
        await self.db.execute("UPDATE energy_orders SET next_try=0 WHERE status='ordering'")
        await self.db.commit()

    async def tick(self, now: float | None = None) -> float:
        """한 번 처리하고 다음 호출까지 기다릴 시간(초)을 돌려준다."""
        now = now or time.time()
        await self._expire(now)
        if now - self._last_catchup >= CATCHUP_EVERY:
            self._last_catchup = now
            await self.catch_up(now)
        await self._fulfil(now)
        await self._verify(now)
        if now - self._last_balance >= BALANCE_EVERY:
            self._last_balance = now
            await self._check_balances(now)
        async with self.db.execute("SELECT COUNT(*) FROM energy_orders WHERE status IN ('paid','ordering','delivered')") as cur:
            busy = (await cur.fetchone())[0]
        return 2.0 if busy else 10.0

    async def _expire(self, now: float) -> None:
        async with self.db.execute("SELECT * FROM energy_orders WHERE status='awaiting' AND expires_at<?", (int(now),)) as cur:
            rows = list(await cur.fetchall())
        for o in rows:
            if await self._move(o["id"], ("awaiting",), "expired"):
                await self.notifier.send(o["user_id"], E.expired(o, LATE_SECONDS // 60))

    async def _fulfil(self, now: float) -> None:
        async with self.db.execute("SELECT * FROM energy_orders WHERE status IN ('paid','ordering') AND next_try<=? "
                                   "ORDER BY id", (int(now),)) as cur:
            rows = list(await cur.fetchall())
        for o in rows:
            if o["status"] == "paid":
                if not await self._move(o["id"], ("paid",), "ordering"):
                    continue
                o = await self.get(o["id"])
            await self._place(o, now)

    async def _place(self, o: aiosqlite.Row, now: float) -> None:
        ref = f"e{o['id']}"
        replay = bool(o["supplier"])
        if replay:                                # 이미 한 곳에 보냈는데 결과를 모른다 → 그곳에만 같은 번호로 다시 묻는다
            s = self.suppliers.get(o["supplier"])
            if s is None:
                await self._to_review(o, o["supplier"], "결과를 모르는 공급처가 설정에서 빠졌습니다", now)
                return
            if not s.idempotent:                  # 다시 보내면 두 번 살 수 있다 → 체인에서 에너지가 왔는지만 본다
                await self._check_chain(o, s.name, "결과를 모르는 채로 재시작", now)
                return
            order = [(0, s)]
        else:
            tried = set(filter(None, o["tried"].split(",")))
            order = [(c, s) for c, s in await self._costs(o["energy"]) if s.name not in tried]
        if not order:
            await self._give_up(o, "모든 공급처가 거절했거나 가격을 가져오지 못했습니다", now)
            return
        for cost, s in order:
            if not o["supplier"] and cost > o["amount"] + self.max_loss:
                await self._give_up(o, f"가장 싼 원가 {cost / SUN:.3f} TRX가 받은 금액보다 너무 비쌉니다", now)
                return
            await self._move(o["id"], ("ordering",), "ordering", supplier=s.name)
            try:
                try:
                    placed = await s.order(o["receiver"], o["energy"], ref)
                except Rejected as e:
                    if replay:      # 처음 요청이 실제로 접수됐을 수도 있으니 다른 공급처로 넘기지 않는다
                        raise Uncertain(f"다시 물었더니 거절: {e}") from e
                    raise
            except Rejected as e:
                log.warning("주문 #%s %s 거절: %s", o["id"], s.name, e)
                if e.low_balance:
                    await self.alert(f"low:{s.name}", E.admin_low(s.name, None), now)
                tried = ",".join(filter(None, [o["tried"], s.name]))
                await self._move(o["id"], ("ordering",), "ordering", supplier="", tried=tried, note=str(e)[:200])
                o = await self.get(o["id"])
                continue
            except Uncertain as e:
                attempts = o["attempts"] + 1
                log.warning("주문 #%s %s 응답 불확실(%d): %s", o["id"], s.name, attempts, e)
                if not s.idempotent:
                    await self._check_chain(o, s.name, str(e), now)
                elif attempts >= UNCERTAIN_MAX:
                    await self._move(o["id"], ("ordering",), "ordering", attempts=attempts)
                    await self._to_review(o, s.name, str(e), now)
                else:
                    await self._move(o["id"], ("ordering",), "ordering", attempts=attempts,
                                     next_try=int(now) + UNCERTAIN_RETRY, note=f"{s.name} 불확실: {e}"[:200])
                return
            await self._move(o["id"], ("ordering",), "delivered", supplier_ref=placed.order_id, delegate_tx=placed.txid,
                             paid_cost=placed.cost or cost, delivered_at=int(now))
            self.wake.set()
            return
        await self._give_up(await self.get(o["id"]), "모든 공급처가 거절했습니다", now)

    async def _check_chain(self, o: aiosqlite.Row, supplier: str, why: str, now: float) -> None:
        """접수됐는지 모르는 주문: 다시 보내지 않고, 정해진 시간 안에 체인에 에너지가 보이면 완료로 본다(아니면 사람이 확인)."""
        await self._move(o["id"], ("ordering",), "delivered", delivered_at=int(now), note=f"{supplier} 불확실: {why}"[:200])
        self.wake.set()

    async def _to_review(self, o: aiosqlite.Row, supplier: str, why: str, now: float) -> None:
        if await self._move(o["id"], ("ordering",), "review", note=f"{supplier} 불확실: {why}"[:200]):
            await self.notifier.send(o["user_id"], E.review(o))
            await self.alert(f"rev:{o['id']}", E.admin_review(o, supplier, why), now, 0)

    async def _give_up(self, o: aiosqlite.Row, why: str, now: float) -> None:
        if await self._move(o["id"], ("ordering",), "failed", note=why[:200]):
            await self.notifier.send(o["user_id"], E.failed(o))
            await self.alert(f"fail:{o['id']}", E.admin_failed(o, why), now, 0)

    async def _verify(self, now: float) -> None:
        async with self.db.execute("SELECT * FROM energy_orders WHERE status='delivered'") as cur:
            rows = list(await cur.fetchall())
        for o in rows:
            try:
                acct = await self.chain.account(o["receiver"])
            except TronError as e:
                log.info("에너지 확인 실패 #%s: %s", o["id"], e)
                acct = None
            if acct is not None and acct.energy >= o["need"]:
                if await self._move(o["id"], ("delivered",), "done", done_at=int(now)):
                    await self.notifier.send(o["user_id"], E.done(await self.get(o["id"]), acct.energy))
            elif now - (o["delivered_at"] or now) > VERIFY_SECONDS:
                if await self._move(o["id"], ("delivered",), "review", note="공급처 성공 응답 뒤 체인에서 에너지가 보이지 않음"):
                    await self.notifier.send(o["user_id"], E.slow(o))
                    await self.alert(f"rev:{o['id']}", E.admin_review(o, o["supplier"], "체인에서 에너지 미확인"), now, 0)

    async def _check_balances(self, now: float) -> None:
        for s in self.suppliers.values():
            try:
                bal = await s.balance()
            except SupplierError as e:
                log.warning("%s 잔고 조회 실패: %s", s.name, e)
                self.balances[s.name] = None
                continue
            self.balances[s.name] = bal
            if bal < self.low_balance:
                await self.alert(f"low:{s.name}", E.admin_low(s.name, bal), now, 6 * 3600)

    # ───────────── 관리자 ─────────────

    async def stats(self, since: int) -> dict:
        async with self.db.execute(
            "SELECT status, COUNT(*) AS n, COALESCE(SUM(amount),0) AS amount, COALESCE(SUM(paid_cost),0) AS cost "
            "FROM energy_orders WHERE created_at>=? GROUP BY status", (since,)) as cur:
            return {r["status"]: dict(r) for r in await cur.fetchall()}

    async def attention(self) -> list[aiosqlite.Row]:
        async with self.db.execute("SELECT * FROM energy_orders WHERE status IN ('failed','review') ORDER BY id") as cur:
            return list(await cur.fetchall())

    async def resolve(self, order_id: int, note: str) -> bool:
        return await self._move(order_id, ("failed", "review"), "resolved", note=note[:200])
