"""DB 스키마와 질의."""
from __future__ import annotations

import sqlite3
import time

import aiosqlite

MAX_WALLETS_PER_USER = 3
MAX_RULES_PER_USER = 3
MAX_DAILY_TIMES = 3
DEFAULT_MIN = 100_000        # 0.1 (마이크로 단위). 소액 스팸 입금을 걸러 내는 기본값

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL,
  address     TEXT NOT NULL,
  label       TEXT NOT NULL DEFAULT '',
  created_at  INTEGER NOT NULL,
  UNIQUE (user_id, address)
);
CREATE INDEX IF NOT EXISTS idx_wallets_addr ON wallets(address);

-- 주소별 조회 위치. 같은 주소를 여러 사람이 등록해도 TronGrid는 한 번만 조회한다
CREATE TABLE IF NOT EXISTS watch_state (
  address    TEXT PRIMARY KEY,
  cursor_ms  INTEGER NOT NULL
);

-- 이미 알린 입금. 겹쳐 조회해도 같은 입금을 두 번 알리지 않는다
CREATE TABLE IF NOT EXISTS notified (
  wallet_id  INTEGER NOT NULL,
  txid       TEXT NOT NULL,
  token      TEXT NOT NULL,
  PRIMARY KEY (wallet_id, txid, token)
);

CREATE TABLE IF NOT EXISTS settings (
  user_id    INTEGER PRIMARY KEY,
  min_usdt   INTEGER NOT NULL DEFAULT 100000,
  min_trx    INTEGER NOT NULL DEFAULT 100000,
  filtered   INTEGER NOT NULL DEFAULT 0,
  daily_on   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kimp_rules (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL,
  direction  TEXT NOT NULL,
  pct        REAL NOT NULL,
  armed      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_times (
  user_id    INTEGER NOT NULL,
  hhmm       TEXT NOT NULL,
  last_sent  TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (user_id, hhmm)
);
"""


# ---- 지갑 ----

async def wallets_of(db: aiosqlite.Connection, user_id: int) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM wallets WHERE user_id=? ORDER BY id", (user_id,)) as cur:
        return list(await cur.fetchall())


async def get_wallet(db: aiosqlite.Connection, wallet_id: int, user_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM wallets WHERE id=? AND user_id=?", (wallet_id, user_id)) as cur:
        return await cur.fetchone()


async def watched_total(db: aiosqlite.Connection) -> int:
    async with db.execute("SELECT COUNT(DISTINCT address) FROM wallets") as cur:
        return (await cur.fetchone())[0]


async def address_is_watched(db: aiosqlite.Connection, address: str) -> bool:
    async with db.execute("SELECT 1 FROM wallets WHERE address=? LIMIT 1", (address,)) as cur:
        return await cur.fetchone() is not None


async def add_wallet(db: aiosqlite.Connection, user_id: int, address: str, label: str,
                     now: float | None = None) -> int | None:
    """등록하고 id를 돌려준다. 같은 사용자가 같은 주소를 이미 등록했으면 None."""
    now = int(now or time.time())
    try:
        cur = await db.execute("INSERT INTO wallets(user_id, address, label, created_at) VALUES(?,?,?,?)",
                               (user_id, address, label, now))
    except sqlite3.IntegrityError:
        return None
    # 이미 지켜보던 주소면 조회 위치를 그대로 두고, 새 주소면 지금부터 본다(과거 입금은 알리지 않는다)
    await db.execute("INSERT OR IGNORE INTO watch_state(address, cursor_ms) VALUES(?,?)", (address, now * 1000))
    await db.commit()
    return cur.lastrowid


async def rename_wallet(db: aiosqlite.Connection, wallet_id: int, label: str) -> None:
    await db.execute("UPDATE wallets SET label=? WHERE id=?", (label, wallet_id))
    await db.commit()


async def delete_wallet(db: aiosqlite.Connection, wallet_id: int) -> None:
    async with db.execute("SELECT address FROM wallets WHERE id=?", (wallet_id,)) as cur:
        row = await cur.fetchone()
    await db.execute("DELETE FROM wallets WHERE id=?", (wallet_id,))
    await db.execute("DELETE FROM notified WHERE wallet_id=?", (wallet_id,))
    if row and not await address_is_watched(db, row["address"]):
        await db.execute("DELETE FROM watch_state WHERE address=?", (row["address"],))
    await db.commit()


async def watched_addresses(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    """조회할 주소와 위치. 봇을 막은(비활성) 사용자의 지갑만 남은 주소는 조회하지 않는다."""
    async with db.execute(
        "SELECT w.address AS address, s.cursor_ms AS cursor_ms FROM wallets w"
        " JOIN users u ON u.user_id = w.user_id AND u.is_active = 1"
        " JOIN watch_state s ON s.address = w.address GROUP BY w.address"
    ) as cur:
        return list(await cur.fetchall())


async def wallets_at(db: aiosqlite.Connection, address: str) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT w.* FROM wallets w JOIN users u ON u.user_id = w.user_id AND u.is_active = 1 WHERE w.address=?",
        (address,),
    ) as cur:
        return list(await cur.fetchall())


async def set_cursor(db: aiosqlite.Connection, address: str, cursor_ms: int) -> None:
    await db.execute("UPDATE watch_state SET cursor_ms=? WHERE address=?", (cursor_ms, address))
    await db.commit()


async def claim_tx(db: aiosqlite.Connection, wallet_id: int, txid: str, token: str) -> bool:
    """이 지갑에서 처음 보는 입금이면 기록하고 True."""
    try:
        await db.execute("INSERT INTO notified(wallet_id, txid, token) VALUES(?,?,?)", (wallet_id, txid, token))
    except sqlite3.IntegrityError:
        return False
    await db.commit()
    return True


async def unclaim_tx(db: aiosqlite.Connection, wallet_id: int, txid: str, token: str) -> None:
    await db.execute("DELETE FROM notified WHERE wallet_id=? AND txid=? AND token=?", (wallet_id, txid, token))
    await db.commit()


# ---- 설정 ----

async def get_settings(db: aiosqlite.Connection, user_id: int) -> aiosqlite.Row:
    await db.execute("INSERT OR IGNORE INTO settings(user_id) VALUES(?)", (user_id,))
    await db.commit()
    async with db.execute("SELECT * FROM settings WHERE user_id=?", (user_id,)) as cur:
        return await cur.fetchone()


async def set_min(db: aiosqlite.Connection, user_id: int, token: str, micro: int) -> None:
    await get_settings(db, user_id)
    col = {"USDT": "min_usdt", "TRX": "min_trx"}[token]
    await db.execute(f"UPDATE settings SET {col}=? WHERE user_id=?", (micro, user_id))
    await db.commit()


async def count_filtered(db: aiosqlite.Connection, user_id: int) -> None:
    await get_settings(db, user_id)
    await db.execute("UPDATE settings SET filtered=filtered+1 WHERE user_id=?", (user_id,))
    await db.commit()


async def set_daily(db: aiosqlite.Connection, user_id: int, on: bool) -> None:
    await get_settings(db, user_id)
    await db.execute("UPDATE settings SET daily_on=? WHERE user_id=?", (1 if on else 0, user_id))
    await db.commit()


# ---- 김프 조건 ----

async def rules_of(db: aiosqlite.Connection, user_id: int) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM kimp_rules WHERE user_id=? ORDER BY id", (user_id,)) as cur:
        return list(await cur.fetchall())


async def add_rule(db: aiosqlite.Connection, user_id: int, direction: str, pct: float) -> None:
    await db.execute("INSERT INTO kimp_rules(user_id, direction, pct) VALUES(?,?,?)", (user_id, direction, pct))
    await db.commit()


async def delete_rule(db: aiosqlite.Connection, rule_id: int, user_id: int) -> None:
    await db.execute("DELETE FROM kimp_rules WHERE id=? AND user_id=?", (rule_id, user_id))
    await db.commit()


async def active_rules(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT r.* FROM kimp_rules r JOIN users u ON u.user_id = r.user_id AND u.is_active = 1"
    ) as cur:
        return list(await cur.fetchall())


async def set_armed(db: aiosqlite.Connection, rule_id: int, armed: bool) -> None:
    await db.execute("UPDATE kimp_rules SET armed=? WHERE id=?", (1 if armed else 0, rule_id))
    await db.commit()


# ---- 정기 시세 ----

async def daily_times(db: aiosqlite.Connection, user_id: int) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM daily_times WHERE user_id=? ORDER BY hhmm", (user_id,)) as cur:
        return list(await cur.fetchall())


async def add_daily(db: aiosqlite.Connection, user_id: int, hhmm: str) -> bool:
    try:
        await db.execute("INSERT INTO daily_times(user_id, hhmm) VALUES(?,?)", (user_id, hhmm))
    except sqlite3.IntegrityError:
        return False
    await db.commit()
    return True


async def delete_daily(db: aiosqlite.Connection, user_id: int, hhmm: str) -> None:
    await db.execute("DELETE FROM daily_times WHERE user_id=? AND hhmm=?", (user_id, hhmm))
    await db.commit()


async def due_daily(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT d.* FROM daily_times d JOIN settings s ON s.user_id=d.user_id AND s.daily_on=1"
        " JOIN users u ON u.user_id=d.user_id AND u.is_active=1"
    ) as cur:
        return list(await cur.fetchall())


async def mark_daily_sent(db: aiosqlite.Connection, user_id: int, hhmm: str, day: str) -> bool:
    """오늘 이 시각 알림을 아직 안 보냈으면 보냈다고 표시하고 True (두 번 보내지 않게)."""
    cur = await db.execute("UPDATE daily_times SET last_sent=? WHERE user_id=? AND hhmm=? AND last_sent<>?",
                           (day, user_id, hhmm, day))
    await db.commit()
    return cur.rowcount == 1
