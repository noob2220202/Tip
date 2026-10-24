"""주문 테이블과 상태 전이.

상태:  awaiting_payment → paid → submitting → processing → completed
       그 밖: expired, cancelled, manual_review(사람이 확인해야 함)
돈이 걸린 전이는 전부 `UPDATE … WHERE status=?` 한 문장으로 하고 영향 행 수를 확인한다 (중복 처리 방지).
"""
from __future__ import annotations

import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id        INTEGER NOT NULL,
  product_id     TEXT NOT NULL,
  product_name   TEXT NOT NULL,
  service_id     INTEGER NOT NULL,
  quantity       INTEGER NOT NULL,
  link           TEXT NOT NULL,
  from_addr      TEXT NOT NULL,
  amount_micro   INTEGER NOT NULL,
  status         TEXT NOT NULL,
  txid           TEXT,
  panel_order_id TEXT,
  panel_status   TEXT,
  attempts       INTEGER NOT NULL DEFAULT 0,
  next_try       INTEGER NOT NULL DEFAULT 0,
  checked_at     INTEGER NOT NULL DEFAULT 0,
  note           TEXT,
  created_at     INTEGER NOT NULL,
  expires_at     INTEGER NOT NULL,
  paid_at        INTEGER,
  done_at        INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_txid ON orders(txid) WHERE txid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, id);

-- 지갑으로 들어온 전송 기록. 한 번 본 전송은 다시 처리하지 않는다.
CREATE TABLE IF NOT EXISTS transfers (
  txid     TEXT PRIMARY KEY,
  sender   TEXT NOT NULL,
  value    INTEGER NOT NULL,
  ts       INTEGER NOT NULL,
  kind     TEXT NOT NULL,       -- matched | late | mismatch | unknown
  order_id INTEGER,
  seen_at  INTEGER NOT NULL
);
"""

OPEN_LIMIT = 3          # 사용자당 동시에 열어 둘 수 있는 입금 대기 주문 수
CANCELLABLE = ("awaiting_payment",)


async def create(db: aiosqlite.Connection, *, user_id: int, product, quantity: int, link: str, from_addr: str,
                 ttl_seconds: int, now: int | None = None) -> int | None:
    """주문을 만든다. 입금 대기 주문이 한도를 넘었으면 None."""
    now = int(now if now is not None else time.time())
    async with db.execute(
        "SELECT COUNT(*) FROM orders WHERE user_id=? AND status='awaiting_payment' AND expires_at>?", (user_id, now)
    ) as cur:
        if (await cur.fetchone())[0] >= OPEN_LIMIT:
            return None
    cur = await db.execute(
        """INSERT INTO orders (user_id, product_id, product_name, service_id, quantity, link, from_addr,
                               amount_micro, status, created_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?, 'awaiting_payment', ?, ?)""",
        (user_id, product.id, product.name, product.service_id, quantity, link, from_addr,
         product.price_micro * quantity, now, now + ttl_seconds),
    )
    await db.commit()
    return cur.lastrowid


async def get(db: aiosqlite.Connection, order_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as cur:
        return await cur.fetchone()


async def by_user(db: aiosqlite.Connection, user_id: int, limit: int = 8) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)) as cur:
        return list(await cur.fetchall())


async def recent(db: aiosqlite.Connection, status: str | None = None, limit: int = 15) -> list[aiosqlite.Row]:
    if status:
        q, a = "SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
    else:
        q, a = "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
    async with db.execute(q, a) as cur:
        return list(await cur.fetchall())


async def transition(db: aiosqlite.Connection, order_id: int, frm: str | tuple[str, ...], to: str, **fields: object) -> bool:
    """frm 상태일 때만 to로 바꾼다. 바뀌었으면 True."""
    frm_t = (frm,) if isinstance(frm, str) else frm
    sets = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
    marks = ",".join("?" * len(frm_t))
    cur = await db.execute(
        f"UPDATE orders SET {sets} WHERE id=? AND status IN ({marks})",
        (to, *fields.values(), order_id, *frm_t),
    )
    await db.commit()
    return cur.rowcount == 1


async def expire_due(db: aiosqlite.Connection, now: int) -> int:
    cur = await db.execute(
        "UPDATE orders SET status='expired' WHERE status='awaiting_payment' AND expires_at<=?", (now,)
    )
    await db.commit()
    return cur.rowcount


async def open_since(db: aiosqlite.Connection, now: int, window: int = 6 * 3600) -> int | None:
    """아직 확인할 입금이 있을 수 있는 가장 오래된 주문 생성 시각(초). 없으면 None."""
    async with db.execute(
        "SELECT MIN(created_at) FROM orders WHERE txid IS NULL AND ("
        " status='awaiting_payment' OR (status IN ('expired','cancelled') AND expires_at>?))",
        (now - window,),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def sales(db: aiosqlite.Connection) -> dict:
    async with db.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(amount_micro),0) AS micro, COALESCE(SUM(quantity),0) AS boosts
           FROM orders WHERE status IN ('paid','submitting','processing','completed')"""
    ) as cur:
        paid = dict(await cur.fetchone())
    async with db.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status") as cur:
        by_status = {r["status"]: r["n"] for r in await cur.fetchall()}
    return {**paid, "by_status": by_status}
