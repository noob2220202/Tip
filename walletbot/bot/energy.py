"""에너지 임대: 주문 → TRX 입금 확인 → 공급처에 주문 → 체인에서 에너지 도착 확인.

가격: 공급처 세 곳의 1시간 원가 중 가장 싼 값 + 마진(기본 0)을 0.001 TRX 단위로 올린 값.
입금 구분: 주문마다 금액 끝자리를 다르게 해서(0.001 TRX 단위) 금액만으로 주문을 찾는다. 메모는 쓰지 않는다(1 TRX 수수료).
  보내는 주소를 묻지 않으므로 거래소에서 보내도 된다. 에너지는 주문할 때 입력한 주소로 간다.
  한 주문의 금액은 그 주문이 입금을 받을 수 있는 동안(만료 후 30분 + 여유) 다른 주문에 다시 쓰지 않는다.
  그래서 같은 금액을 두 번 보내도 다음 손님 주문에 잡히지 않고 관리자 알림으로 간다.
입금 확인: 블록 스캔이 바로 잡고, 결제 주소 내역을 저장된 위치(커서)부터 주기적으로 다시 훑어서
  봇이나 TronGrid가 오래 멈췄던 동안의 입금도 놓치지 않는다.
중복 구매 방지: paid → ordering으로 먼저 바꾼 뒤에만 공급처를 부른다. 응답이 불확실하면 다른 공급처로 넘기지 않고
  같은 공급처에 같은 주문 번호(ref)로만 다시 묻는다(그 번호의 두 번째 요청을 새 주문으로 만들지 않는 곳만).
  그렇지 않은 공급처는 다시 보내지 않고 체인에서 에너지가 들어왔는지만 본다. 예상하지 못한 오류도 '불확실'로 다룬다.
서버에는 개인키가 없다. 환불이 필요한 경우(금액 불일치, 모든 공급처 실패)는 관리자에게 알리고,
  손님에게 환불받을 본인 지갑 주소를 받아 관리자에게 넘긴다. 환불은 관리자가 직접 한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import secrets
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
-- ref는 공급처에 보내는 주문 번호(설치·DB 복원과 상관없이 겹치지 않게 무작위), refund_to는 손님이 알려 준 환불 주소
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
  note          TEXT NOT NULL DEFAULT '',
  ref           TEXT NOT NULL DEFAULT '',
  refund_to     TEXT NOT NULL DEFAULT ''
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

-- 결제 주소 내역을 어디까지 확인했는지(한 줄)
CREATE TABLE IF NOT EXISTS energy_state (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  pay_cursor_ms INTEGER NOT NULL
);
"""

# 먼저 배포된 DB에 나중에 추가한 열
_COLUMNS = {"ref": "TEXT NOT NULL DEFAULT ''", "refund_to": "TEXT NOT NULL DEFAULT ''"}

SUN = 1_000_000
STEP = 1_000                 # 금액 단위 0.001 TRX. 같은 원가의 주문끼리는 이 단위로 끝자리를 다르게 한다
SLOTS = 100                  # 끝자리 후보 수 (최대 +0.099 TRX)
BUFFER = 500                 # 모의 실행 값에 더하는 여유 에너지
LATE_SECONDS = 1800          # 만료 뒤 이 시간 안에 들어온 정확한 금액은 그대로 처리한다(거래소 출금 지연 등)
CLOCK_SLACK = 120            # 블록 시각과 서버 시각 차이를 감안한 여유(초)
DUST = 500_000               # 0.5 TRX 미만 입금은 스팸으로 보고 무시한다
CATCHUP_EVERY = 30           # 결제 주소 내역을 다시 훑는 간격(초). 블록 스캔이 놓친 입금 보충
CATCHUP_OVERLAP = 600        # 내역은 확인한 위치보다 이만큼(초) 겹쳐서 다시 본다(확정 지연·시각 차이)
FIRST_LOOKBACK = 86400       # 확인 위치가 아직 없을 때(처음 켤 때) 거슬러 보는 시간(초)
UNCERTAIN_RETRY = 20         # 불확실한 응답 뒤 같은 공급처에 다시 묻는 간격(초)
UNCERTAIN_MAX = 6            # 이만큼 불확실하면 사람이 확인한다
PRICE_RETRY = 30             # 가격을 하나도 못 가져왔을 때 다시 해 보는 간격(초). 아직 아무것도 사지 않았다
PRICE_RETRY_MAX = 10         # 이만큼(약 5분) 계속 못 가져오면 실패로 넘긴다
VERIFY_SECONDS = 120         # 공급처 성공 뒤 체인에서 에너지가 보일 때까지 기다리는 시간
REVIEW_WATCH = 3600          # 확인 필요로 넘긴 주문도 결제 뒤 이 시간 동안은 체인에서 에너지가 보이는지 계속 본다
REVIEW_EVERY = 15            # 그 확인 간격(초)
SHARED_WINDOW = 3600         # 같은 지갑의 다른 주문이 이 시간 안에 결제됐으면 보이는 에너지가 어느 주문 것인지 알 수 없다
REPLAY_MAX = 23 * 3600       # 결제 후 이보다 오래 지난 주문은 같은 번호로 다시 보내지 않는다(Netts 중복 방지 키는 24시간)
BALANCE_EVERY = 600          # 공급처 잔고 확인 간격(초)
ALERT_EVERY = 1800
USER_ORDERS_PER_HOUR = 10    # 결제 없이 주문만 반복해 끝자리를 다 쓰지 못하게

# 받는 쪽 USDT 보유 여부만 알 때 모의 실행에 쓰는 주소. HOLDER는 USDT가 많은 주소, EMPTY는 아무도 쓸 수 없는 빈 주소
SIM_OWNER = "TV6MuMXfmLbBqPZvBHdwFsDnQeVfnmiuSi"
SIM_HOLDER = "TNXoiAJ3dct8Fjg4M9fkLFh9S2v9TXc32G"
SIM_EMPTY = hex_to_address("41" + hashlib.sha256(b"walletbot energy empty receiver").hexdigest()[:40])
# 모의 실행이 모두 실패했을 때 쓰는 값 (2026-10 기준 USDT 전송 실측: 받는 쪽에 USDT 있음 64,285 / 없음 130,285)
FALLBACK_NEED = {True: 64_285, False: 130_285}

REFUNDABLE = ("failed", "review", "awaiting", "expired", "cancelled")


def order_energy(need: int, minimum: int) -> int:
    return max(minimum, math.ceil((need + BUFFER) / 1000) * 1000)


async def migrate(db: aiosqlite.Connection) -> None:
    """먼저 만들어진 energy_orders 표에 없는 열을 더한다."""
    async with db.execute("PRAGMA table_info(energy_orders)") as cur:
        have = {r[1] for r in await cur.fetchall()}
    for col, ddl in _COLUMNS.items():
        if have and col not in have:
            await db.execute(f"ALTER TABLE energy_orders ADD COLUMN {col} {ddl}")
    await db.commit()


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
                 low_balance: int = 50 * SUN, support: str = "") -> None:
        self.db, self.chain, self.notifier = db, chain, notifier
        self.suppliers = {s.name: s for s in suppliers}
        self.pay_address, self.pay_hex = pay_address, address_to_hex(pay_address) if pay_address else None
        self.margin, self.max_loss, self.ttl, self.low_balance = margin, max_loss, ttl, low_balance
        self.support = support
        self.wake = asyncio.Event()
        self._create_lock = asyncio.Lock()
        self._last_catchup = 0.0
        self._last_balance = 0.0
        self._last_review = 0.0
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

    async def who(self, user_id: int) -> str | None:
        """관리자 알림에 붙일 손님 username (없으면 None)."""
        async with self.db.execute("SELECT username FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        return row["username"] if row and row["username"] else None

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
        """공급처별 원가, 싼 순서. 가격을 못 가져온 곳은 뺀다(어떤 오류든 한 곳 때문에 나머지가 막히지 않게)."""
        async def one(s: Supplier):
            if energy < s.min_energy:
                return None
            try:
                return await s.cost(energy), s
            except SupplierError as e:
                log.warning("%s 가격 조회 실패: %s", s.name, e)
            except Exception:  # noqa: BLE001 - 가격 조회는 부작용이 없다
                log.exception("%s 가격 조회 오류", s.name)
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
        """주문을 만든다. 끝자리를 배정할 자리가 없으면 None.

        입금을 아직 받을 수 있는 주문(대기 중이거나, 만료 후 30분 + 여유가 안 지난 주문)의 금액은 상태와 상관없이 비켜 간다.
        """
        now = int(now or time.time())
        async with self._create_lock:
            async with self.db.execute(
                "SELECT amount FROM energy_orders WHERE status='awaiting' OR expires_at>=?",
                (now - LATE_SECONDS - CLOCK_SLACK,),
            ) as cur:
                used = {r[0] for r in await cur.fetchall()}
            amount = next((q.price + k * STEP for k in range(SLOTS) if q.price + k * STEP not in used), None)
            if amount is None:
                return None
            await self.db.execute("UPDATE energy_orders SET status='cancelled' WHERE user_id=? AND status='awaiting'",
                                  (user_id,))
            cur = await self.db.execute(
                "INSERT INTO energy_orders(user_id, receiver, need, energy, amount, cost, status, created_at, expires_at, ref)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (user_id, receiver, need, q.energy, amount, q.cost, "awaiting", now, now + self.ttl,
                 "e" + secrets.token_hex(8)),
            )
            await self.db.commit()
        return await self.get(cur.lastrowid)

    async def cancel(self, order_id: int, user_id: int) -> bool:
        cur = await self.db.execute("UPDATE energy_orders SET status='cancelled' WHERE id=? AND user_id=? "
                                    "AND status='awaiting'", (order_id, user_id))
        await self.db.commit()
        return cur.rowcount == 1

    async def mismatches(self, order_id: int) -> list[aiosqlite.Row]:
        """이 주문 손님의 지갑에서 들어온, 금액이 다른 입금(환불 대상)."""
        async with self.db.execute("SELECT * FROM energy_payments WHERE order_id=? AND kind='mismatch' ORDER BY ts_ms",
                                   (order_id,)) as cur:
            return list(await cur.fetchall())

    async def can_refund(self, o: aiosqlite.Row) -> bool:
        """환불 주소를 받을 수 있는 주문: 처리되지 못한 주문이거나, 금액이 다른 입금이 따로 들어온 주문."""
        return o["status"] in REFUNDABLE or bool(await self.mismatches(o["id"]))

    async def set_refund(self, order_id: int, user_id: int, address: str) -> aiosqlite.Row | None:
        """손님이 알려 준 환불 주소를 남기고 관리자에게 알린다."""
        o = await self.get(order_id)
        if o is None or o["user_id"] != user_id or not await self.can_refund(o):
            return None
        await self.db.execute("UPDATE energy_orders SET refund_to=? WHERE id=?", (address, order_id))
        await self.db.commit()
        o = await self.get(order_id)
        await self.notifier.admins(E.admin_refund_to(o, await self.who(user_id), await self.mismatches(order_id)))
        return o

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
                await self.notifier.send(order["user_id"], E.mismatch(order, d.value), E.refund_kb(order["id"]))
                await self.alert(f"pay:{d.txid}", E.admin_unknown(d, f"금액 불일치 — 주문 #{order['id']}", order,
                                                                  await self.who(order["user_id"])), now, 0)
            elif kind == "unknown":
                await self.alert(f"pay:{d.txid}", E.admin_unknown(d, "맞는 주문 없음"), now, 0)
        if hit:
            self.wake.set()

    async def _match(self, d: Deposit, now: int) -> tuple[str, aiosqlite.Row | None]:
        if d.token != "TRX":
            return ("usdt", None) if d.value >= SUN else ("dust", None)
        # 입금을 받을 수 있는 같은 금액의 주문. 주문한 지갑에서 보낸 것, 입금보다 먼저 만든 것을 먼저 고른다
        async with self.db.execute(
            "SELECT * FROM energy_orders WHERE amount=? AND txid IS NULL AND created_at*1000<=? AND "
            "(status='awaiting' OR (status IN ('expired','cancelled') AND expires_at*1000>=?)) "
            "ORDER BY (receiver=?) DESC, (created_at*1000<=?) DESC, id",
            (d.value, d.ts_ms + CLOCK_SLACK * 1000, d.ts_ms - LATE_SECONDS * 1000, d.sender, d.ts_ms),
        ) as cur:
            cands = list(await cur.fetchall())
        for o in cands:
            if await self._move(o["id"], ("awaiting", "expired", "cancelled"), "paid", txid=d.txid, payer=d.sender,
                                paid_at=now, next_try=0, attempts=0):
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

    async def _cursor(self) -> int | None:
        async with self.db.execute("SELECT pay_cursor_ms FROM energy_state WHERE id=1") as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def _set_cursor(self, ms: int) -> None:
        await self.db.execute("INSERT INTO energy_state(id, pay_cursor_ms) VALUES(1,?) "
                              "ON CONFLICT(id) DO UPDATE SET pay_cursor_ms=excluded.pay_cursor_ms", (ms,))
        await self.db.commit()

    async def catch_up(self, now: float) -> None:
        """결제 주소 내역을 마지막으로 확인한 위치부터 다시 훑는다(봇이 꺼졌거나 스캔이 밀려 건너뛴 블록 보충).

        확인 위치는 성공했을 때만 앞으로 옮기므로, 오래 멈췄다가 돌아와도 그 사이의 입금을 블록 시각 기준으로 맞춘다.
        """
        if not self.enabled:
            return
        started = int(now * 1000)
        cursor = await self._cursor()
        seen = cursor // 1000 if cursor is not None else int(now) - FIRST_LOOKBACK
        # 아직 입금을 받을 수 있었던 주문(확인 위치 이후에 창이 열려 있던 주문)이 있을 때만 조회한다
        async with self.db.execute(
            "SELECT MIN(created_at) FROM energy_orders WHERE txid IS NULL AND status IN ('awaiting','expired','cancelled') "
            "AND expires_at>=?", (min(seen, int(now)) - LATE_SECONDS - CATCHUP_OVERLAP,),
        ) as cur:
            oldest = (await cur.fetchone())[0]
        if oldest is None:
            await self._set_cursor(started)
            return
        since = oldest * 1000 - CLOCK_SLACK * 1000
        if cursor is not None:
            since = max(since, cursor - CATCHUP_OVERLAP * 1000)
        try:
            found = await self.chain.deposits(self.pay_address, since)
        except RateLimited:
            return
        except TronError as e:
            log.warning("결제 주소 내역 조회 실패: %s", e)
            return
        await self.on_deposits([Deposit(d.txid, d.token, d.sender, d.value, d.ts_ms, self.pay_address) for d in found], now)
        await self._set_cursor(started)

    # ───────────── 처리 루프 ─────────────

    async def recover(self) -> None:
        """재시작 직후: 공급처를 부르던 중이던 주문은 같은 번호로 다시 묻도록 대기열에 둔다(새 주문을 만들지 않는다)."""
        await self.db.execute("UPDATE energy_orders SET next_try=0 WHERE status='ordering'")
        await self.db.commit()

    async def tick(self, now: float | None = None) -> float:
        """한 번 처리하고 다음 호출까지 기다릴 시간(초)을 돌려준다. 단계마다 따로 오류를 잡아 하나가 나머지를 막지 않게 한다."""
        now = now or time.time()
        steps = []
        if now - self._last_catchup >= CATCHUP_EVERY:      # 만료 처리보다 먼저: 멈춘 사이에 들어온 입금을 먼저 맞춘다
            self._last_catchup = now
            steps.append(self.catch_up)
        steps += [self._expire, self._fulfil, self._verify]
        if now - self._last_review >= REVIEW_EVERY:
            self._last_review = now
            steps.append(self._watch_review)
        if now - self._last_balance >= BALANCE_EVERY:
            self._last_balance = now
            steps.append(self._check_balances)
        for step in steps:
            try:
                await step(now)
            except Exception:  # noqa: BLE001
                log.exception("에너지 처리 오류 (%s)", step.__name__)
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
            try:
                await self._place(o, now)
            except Exception as e:  # noqa: BLE001 - 주문 하나의 오류가 다른 주문을 막지 않게
                log.exception("에너지 주문 #%s 처리 오류", o["id"])
                await self._unexpected(o["id"], f"처리 오류: {e!r}", now)

    def ref_of(self, o: aiosqlite.Row) -> str:
        return o["ref"] or f"e{o['id']}"

    async def _place(self, o: aiosqlite.Row, now: float) -> None:
        ref = self.ref_of(o)
        replay = bool(o["supplier"])
        if replay:                                # 이미 한 곳에 보냈는데 결과를 모른다 → 그곳에만 같은 번호로 다시 묻는다
            s = self.suppliers.get(o["supplier"])
            if s is None:
                await self._to_review(o, o["supplier"], "결과를 모르는 공급처가 설정에서 빠졌습니다", now)
                return
            if now - (o["paid_at"] or now) > REPLAY_MAX:
                await self._to_review(o, s.name, "하루 가까이 멈춘 뒤라 같은 번호로 다시 보내지 않았습니다", now)
                return
            if not s.idempotent:                  # 다시 보내면 두 번 살 수 있다 → 체인에서 에너지가 왔는지만 본다
                await self._check_chain(o, s.name, "결과를 모르는 채로 재시작", now)
                return
            order = [(0, s)]
        else:
            tried = set(filter(None, o["tried"].split(",")))
            order = [(c, s) for c, s in await self._costs(o["energy"]) if s.name not in tried]
            if not order:
                if set(self.suppliers) <= tried:
                    await self._give_up(o, "모든 공급처가 거절했습니다", now)
                elif o["attempts"] + 1 >= PRICE_RETRY_MAX:
                    await self._give_up(o, "공급처 가격을 계속 가져오지 못했습니다", now)
                else:                             # 아직 아무것도 사지 않았으니 잠시 뒤 다시 해 본다
                    await self._move(o["id"], ("ordering",), "ordering", attempts=o["attempts"] + 1,
                                     next_try=int(now) + PRICE_RETRY, note="공급처 가격 조회 실패, 다시 시도")
                return
        for cost, s in order:
            if not replay and cost > o["amount"] + self.max_loss:
                await self._give_up(o, f"가장 싼 원가 {cost / SUN:.3f} TRX가 받은 금액보다 너무 비쌉니다", now)
                return
            if not replay:
                await self._move(o["id"], ("ordering",), "ordering", supplier=s.name, attempts=0)
                o = await self.get(o["id"])
            try:
                try:
                    placed = await s.order(o["receiver"], o["energy"], ref)
                except Rejected as e:
                    if replay:      # 처음 요청이 실제로 접수됐을 수도 있으니 다른 공급처로 넘기지 않는다
                        raise Uncertain(f"다시 물었더니 거절: {e}") from e
                    raise
                except Uncertain:
                    raise
                except Exception as e:  # noqa: BLE001 - 응답을 읽다 난 오류는 청구됐을 수 있으므로 불확실
                    log.exception("주문 #%s %s 예상하지 못한 오류", o["id"], s.name)
                    raise Uncertain(f"예상하지 못한 오류: {e!r}") from e
            except Rejected as e:
                log.warning("주문 #%s %s 거절: %s", o["id"], s.name, e)
                if e.low_balance:
                    await self.alert(f"low:{s.name}", E.admin_low(s.name, None), now)
                tried = ",".join(filter(None, [o["tried"], s.name]))
                await self._move(o["id"], ("ordering",), "ordering", supplier="", tried=tried, note=str(e)[:200])
                o = await self.get(o["id"])
                continue
            except Uncertain as e:
                log.warning("주문 #%s %s 응답 불확실: %s", o["id"], s.name, e)
                await self._unexpected(o["id"], f"{s.name} 불확실: {e}", now)
                return
            await self._move(o["id"], ("ordering",), "delivered", supplier_ref=placed.order_id, delegate_tx=placed.txid,
                             paid_cost=placed.cost or cost, delivered_at=int(now))
            self.wake.set()
            return
        o = await self.get(o["id"])
        if set(self.suppliers) <= set(filter(None, o["tried"].split(","))):
            await self._give_up(o, "모든 공급처가 거절했습니다", now)
        else:                                     # 남은 곳은 가격을 못 가져온 곳뿐 → 잠시 뒤 다시
            await self._move(o["id"], ("ordering",), "ordering", attempts=o["attempts"] + 1,
                             next_try=int(now) + PRICE_RETRY)

    async def _unexpected(self, order_id: int, why: str, now: float) -> None:
        """결과를 모르는 경우: 공급처를 이미 불렀다면 '불확실'과 같게, 아직 안 불렀다면 잠시 뒤 다시."""
        o = await self.get(order_id)
        if o is None or o["status"] != "ordering":
            return
        attempts = o["attempts"] + 1
        s = self.suppliers.get(o["supplier"]) if o["supplier"] else None
        if o["supplier"] and s is not None and not s.idempotent:
            await self._check_chain(o, s.name, why, now)
        elif attempts >= (UNCERTAIN_MAX if o["supplier"] else PRICE_RETRY_MAX):
            await self._move(o["id"], ("ordering",), "ordering", attempts=attempts)
            if o["supplier"]:
                await self._to_review(o, o["supplier"], why, now)
            else:
                await self._give_up(o, why, now)
        else:
            await self._move(o["id"], ("ordering",), "ordering", attempts=attempts,
                             next_try=int(now) + (UNCERTAIN_RETRY if o["supplier"] else PRICE_RETRY), note=why[:200])

    async def _check_chain(self, o: aiosqlite.Row, supplier: str, why: str, now: float) -> None:
        """접수됐는지 모르는 주문: 다시 보내지 않고, 정해진 시간 안에 체인에 에너지가 보이면 완료로 본다(아니면 사람이 확인)."""
        await self._move(o["id"], ("ordering",), "delivered", delivered_at=int(now), note=f"{supplier} 불확실: {why}"[:200])
        self.wake.set()

    async def _to_review(self, o: aiosqlite.Row, supplier: str, why: str, now: float) -> None:
        if await self._move(o["id"], ("ordering",), "review", note=f"{supplier} 불확실: {why}"[:200]):
            o = await self.get(o["id"])
            await self.notifier.send(o["user_id"], E.review(o))
            await self.alert(f"rev:{o['id']}", E.admin_review(o, supplier, why, await self.who(o["user_id"])), now, 0)

    async def _give_up(self, o: aiosqlite.Row, why: str, now: float) -> None:
        if await self._move(o["id"], ("ordering",), "failed", note=why[:200]):
            o = await self.get(o["id"])
            await self.notifier.send(o["user_id"], E.failed(o, self.support), E.refund_kb(o["id"]))
            await self.alert(f"fail:{o['id']}", E.admin_failed(o, why, await self.who(o["user_id"])), now, 0)

    async def _verify(self, now: float) -> None:
        async with self.db.execute("SELECT * FROM energy_orders WHERE status='delivered'") as cur:
            rows = list(await cur.fetchall())
        for o in rows:
            try:
                acct = await self.chain.account(o["receiver"])
            except TronError as e:
                log.info("에너지 확인 실패 #%s: %s", o["id"], e)
                continue                          # 체인을 못 읽었으면 시간 초과로 보지 않는다
            if acct.energy >= o["need"]:
                if not o["supplier_ref"] and await self._shared(o):
                    # 공급처 확인 없이 체인만 보는 주문인데 같은 지갑에 다른 주문도 있었다 → 이 주문의 에너지인지 알 수 없다
                    if await self._move(o["id"], ("delivered",), "review", note="같은 지갑의 다른 주문과 겹쳐 확인 필요"):
                        o = await self.get(o["id"])
                        await self.notifier.send(o["user_id"], E.review(o))
                        await self.alert(f"rev:{o['id']}", E.admin_review(o, o["supplier"], "같은 지갑의 다른 주문과 겹침",
                                                                          await self.who(o["user_id"])), now, 0)
                elif await self._move(o["id"], ("delivered",), "done", done_at=int(now)):
                    await self.notifier.send(o["user_id"], E.done(await self.get(o["id"]), acct.energy))
            elif now - (o["delivered_at"] or now) > VERIFY_SECONDS:
                if await self._move(o["id"], ("delivered",), "review", note="공급처 성공 응답 뒤 체인에서 에너지가 보이지 않음"):
                    o = await self.get(o["id"])
                    await self.notifier.send(o["user_id"], E.slow(o))
                    await self.alert(f"rev:{o['id']}", E.admin_review(o, o["supplier"], "체인에서 에너지 미확인",
                                                                      await self.who(o["user_id"])), now, 0)

    async def _watch_review(self, now: float) -> None:
        """확인 필요로 넘긴 주문도 결제 후 1시간 동안은 체인을 보고, 에너지가 들어오면 손님에게 바로 알린다."""
        async with self.db.execute("SELECT * FROM energy_orders WHERE status='review' AND paid_at>=?",
                                   (int(now) - REVIEW_WATCH,)) as cur:
            rows = list(await cur.fetchall())
        for o in rows:
            try:
                acct = await self.chain.account(o["receiver"])
            except TronError:
                continue
            if acct.energy < o["need"]:
                continue
            if await self._shared(o):              # 같은 지갑의 다른 주문 에너지일 수 있다 → 관리자에게 맡긴다
                await self.alert(f"shared:{o['id']}", E.admin_shared(o), now, REVIEW_WATCH)
                continue
            if await self._move(o["id"], ("review",), "done", done_at=int(now), note="체인에서 에너지 확인되어 자동 완료"):
                await self.notifier.send(o["user_id"], E.done(await self.get(o["id"]), acct.energy))
                await self.alert(f"auto:{o['id']}", E.admin_auto_done(o), now, 0)

    async def _shared(self, o: aiosqlite.Row) -> bool:
        """같은 지갑으로 가는 다른 주문이 비슷한 때 결제됐는지(그러면 보이는 에너지를 이 주문 것이라고 할 수 없다)."""
        async with self.db.execute(
            "SELECT 1 FROM energy_orders WHERE receiver=? AND id<>? AND txid IS NOT NULL AND paid_at>=? LIMIT 1",
            (o["receiver"], o["id"], (o["paid_at"] or 0) - SHARED_WINDOW),
        ) as cur:
            return await cur.fetchone() is not None

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

    async def resolve(self, order_id: int, note: str) -> aiosqlite.Row | None:
        """실패·확인 필요 주문을 처리됨으로 바꾸고 손님에게 알린다."""
        if not await self._move(order_id, ("failed", "review"), "resolved", note=note[:200]):
            return None
        o = await self.get(order_id)
        await self.notifier.send(o["user_id"], E.resolved(o, note))
        return o
