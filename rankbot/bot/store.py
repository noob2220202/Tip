"""DB 스키마와 질의. 모든 테이블이 bot_id로 고객 봇을 구분한다."""
from __future__ import annotations

import sqlite3
import time

import aiosqlite

from bot.schedule import next_occurrence

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
  bot_id           INTEGER PRIMARY KEY,
  owner_id         INTEGER NOT NULL,
  username         TEXT NOT NULL,
  token_enc        TEXT NOT NULL,
  name             TEXT NOT NULL DEFAULT '',
  min_chars        INTEGER NOT NULL DEFAULT 5,
  auto_on          INTEGER NOT NULL DEFAULT 1,
  weekday          INTEGER NOT NULL DEFAULT 0,
  hour             INTEGER NOT NULL DEFAULT 21,
  minute           INTEGER NOT NULL DEFAULT 0,
  prizes           TEXT NOT NULL DEFAULT '',
  qualify          INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'active',
  round            INTEGER NOT NULL DEFAULT 1,
  round_started_at INTEGER NOT NULL,
  next_settle_at   INTEGER NOT NULL,
  created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bots_owner ON bots(owner_id);

CREATE TABLE IF NOT EXISTS chats (
  bot_id   INTEGER NOT NULL,
  chat_id  INTEGER NOT NULL,
  title    TEXT NOT NULL DEFAULT '',
  active   INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (bot_id, chat_id)
);

CREATE TABLE IF NOT EXISTS counts (
  bot_id   INTEGER NOT NULL,
  chat_id  INTEGER NOT NULL,
  round    INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  name     TEXT NOT NULL,
  messages INTEGER NOT NULL DEFAULT 0,
  last_at  REAL NOT NULL,
  PRIMARY KEY (bot_id, chat_id, round, user_id)
);

CREATE TABLE IF NOT EXISTS winners (
  bot_id   INTEGER NOT NULL,
  chat_id  INTEGER NOT NULL,
  round    INTEGER NOT NULL,
  rank     INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  name     TEXT NOT NULL,
  messages INTEGER NOT NULL,
  prize    TEXT NOT NULL,
  PRIMARY KEY (bot_id, chat_id, round, rank)
);

CREATE TABLE IF NOT EXISTS announce_queue (
  bot_id   INTEGER NOT NULL,
  chat_id  INTEGER NOT NULL,
  round    INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bot_id, chat_id, round)
);
"""

EDITABLE = {"name", "min_chars", "auto_on", "weekday", "hour", "minute", "prizes", "qualify", "status", "token_enc"}


async def add_bot(db: aiosqlite.Connection, bot_id: int, owner_id: int, username: str, token_enc: str,
                  name: str, now: float | None = None) -> bool:
    """봇 등록. 이미 등록된 봇이면 False."""
    now = now or time.time()
    try:
        await db.execute(
            "INSERT INTO bots(bot_id, owner_id, username, token_enc, name, round_started_at, next_settle_at, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (bot_id, owner_id, username, token_enc, name, int(now), next_occurrence(0, 21, 0, now), int(now)),
        )
    except sqlite3.IntegrityError:
        return False
    await db.commit()
    return True


async def get_bot(db: aiosqlite.Connection, bot_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM bots WHERE bot_id=?", (bot_id,)) as cur:
        return await cur.fetchone()


async def get_owned(db: aiosqlite.Connection, bot_id: int, user_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM bots WHERE bot_id=? AND owner_id=?", (bot_id, user_id)) as cur:
        return await cur.fetchone()


async def bots_of(db: aiosqlite.Connection, owner_id: int) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM bots WHERE owner_id=? ORDER BY created_at", (owner_id,)) as cur:
        return list(await cur.fetchall())


async def active_bots(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM bots WHERE status='active'") as cur:
        return list(await cur.fetchall())


async def update_bot(db: aiosqlite.Connection, bot_id: int, **fields: object) -> None:
    bad = set(fields) - EDITABLE
    if bad:
        raise ValueError(f"바꿀 수 없는 필드: {bad}")
    sets = ", ".join(f"{k}=?" for k in fields)
    await db.execute(f"UPDATE bots SET {sets} WHERE bot_id=?", (*fields.values(), bot_id))
    await db.commit()


async def set_schedule(db: aiosqlite.Connection, bot_id: int, now: float | None = None, **fields: int) -> None:
    """요일/시/분을 바꾸고 다음 발표 시각을 다시 계산한다. 이번 회차 카운트는 그대로 둔다."""
    await update_bot(db, bot_id, **fields)
    await _recompute_next(db, bot_id, now or time.time())


async def set_auto(db: aiosqlite.Connection, bot_id: int, on: bool, now: float | None = None) -> None:
    await update_bot(db, bot_id, auto_on=1 if on else 0)
    if on:
        await _recompute_next(db, bot_id, now or time.time())


async def _recompute_next(db: aiosqlite.Connection, bot_id: int, now: float) -> None:
    bot = await get_bot(db, bot_id)
    nxt = next_occurrence(bot["weekday"], bot["hour"], bot["minute"], now)
    await db.execute("UPDATE bots SET next_settle_at=? WHERE bot_id=?", (nxt, bot_id))
    await db.commit()


async def delete_bot(db: aiosqlite.Connection, bot_id: int) -> None:
    for table in ("announce_queue", "winners", "counts", "chats", "bots"):
        await db.execute(f"DELETE FROM {table} WHERE bot_id=?", (bot_id,))
    await db.commit()


# ---- 그룹 ----

async def upsert_chat(db: aiosqlite.Connection, bot_id: int, chat_id: int, title: str, active: bool = True) -> None:
    await db.execute(
        "INSERT INTO chats(bot_id, chat_id, title, active) VALUES(?,?,?,?)"
        " ON CONFLICT(bot_id, chat_id) DO UPDATE SET title=excluded.title, active=excluded.active",
        (bot_id, chat_id, title, 1 if active else 0),
    )
    await db.commit()


async def set_chat_active(db: aiosqlite.Connection, bot_id: int, chat_id: int, active: bool) -> None:
    await db.execute("UPDATE chats SET active=? WHERE bot_id=? AND chat_id=?", (1 if active else 0, bot_id, chat_id))
    await db.commit()


async def chat_count(db: aiosqlite.Connection, bot_id: int) -> int:
    async with db.execute("SELECT COUNT(*) FROM chats WHERE bot_id=? AND active=1", (bot_id,)) as cur:
        return (await cur.fetchone())[0]


# ---- 집계 ----

async def record(db: aiosqlite.Connection, bot_id: int, chat_id: int, round_: int, user_id: int, name: str,
                 now: float | None = None) -> None:
    await db.execute(
        "INSERT INTO counts(bot_id, chat_id, round, user_id, name, messages, last_at) VALUES(?,?,?,?,?,1,?)"
        " ON CONFLICT(bot_id, chat_id, round, user_id)"
        " DO UPDATE SET messages=messages+1, name=excluded.name, last_at=excluded.last_at",
        (bot_id, chat_id, round_, user_id, name, now or time.time()),
    )
    await db.commit()


async def ranking(db: aiosqlite.Connection, bot_id: int, chat_id: int, round_: int,
                  limit: int | None = None) -> list[aiosqlite.Row]:
    sql = ("SELECT user_id, name, messages FROM counts WHERE bot_id=? AND chat_id=? AND round=? AND messages>0"
           " ORDER BY messages DESC, last_at ASC, user_id ASC")
    args: list = [bot_id, chat_id, round_]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    async with db.execute(sql, args) as cur:
        return list(await cur.fetchall())


async def my_stats(db: aiosqlite.Connection, bot_id: int, chat_id: int, round_: int,
                   user_id: int) -> tuple[int, int, int]:
    """(내 횟수, 내 순위, 참여자 수). 횟수가 같으면 먼저 달성한 사람이 위."""
    async with db.execute(
        "SELECT messages, last_at FROM counts WHERE bot_id=? AND chat_id=? AND round=? AND user_id=?",
        (bot_id, chat_id, round_, user_id),
    ) as cur:
        me = await cur.fetchone()
    async with db.execute(
        "SELECT COUNT(*) FROM counts WHERE bot_id=? AND chat_id=? AND round=? AND messages>0",
        (bot_id, chat_id, round_),
    ) as cur:
        people = (await cur.fetchone())[0]
    if not me:
        return 0, 0, people
    async with db.execute(
        "SELECT COUNT(*) FROM counts WHERE bot_id=? AND chat_id=? AND round=? AND messages>0"
        " AND (messages>? OR (messages=? AND last_at<?))",
        (bot_id, chat_id, round_, me["messages"], me["messages"], me["last_at"]),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return me["messages"], ahead + 1, people


# ---- 발표 ----

async def due_bots(db: aiosqlite.Connection, now: float) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT * FROM bots WHERE status='active' AND auto_on=1 AND next_settle_at<=?", (int(now),)
    ) as cur:
        return list(await cur.fetchall())


async def advance_round(db: aiosqlite.Connection, bot: aiosqlite.Row, now: float) -> bool:
    """회차를 넘기고 발표할 그룹을 큐에 넣는다. 한 트랜잭션이라 중간에 죽어도 발표가 사라지지 않는다.

    이미 다른 곳에서 넘겼으면(round가 달라졌으면) False.
    """
    nxt = next_occurrence(bot["weekday"], bot["hour"], bot["minute"], now)
    cur = await db.execute(
        "UPDATE bots SET round=round+1, round_started_at=?, next_settle_at=? WHERE bot_id=? AND round=?",
        (int(now), nxt, bot["bot_id"], bot["round"]),
    )
    if cur.rowcount != 1:
        await db.rollback()
        return False
    await db.execute(
        "INSERT OR IGNORE INTO announce_queue(bot_id, chat_id, round)"
        " SELECT c.bot_id, c.chat_id, ? FROM chats c WHERE c.bot_id=? AND c.active=1"
        " AND EXISTS(SELECT 1 FROM counts n WHERE n.bot_id=c.bot_id AND n.chat_id=c.chat_id AND n.round=?"
        " AND n.messages>0)",
        (bot["round"], bot["bot_id"], bot["round"]),
    )
    await db.commit()
    return True


async def queued(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM announce_queue ORDER BY bot_id, chat_id") as cur:
        return list(await cur.fetchall())


async def dequeue(db: aiosqlite.Connection, bot_id: int, chat_id: int, round_: int) -> None:
    await db.execute("DELETE FROM announce_queue WHERE bot_id=? AND chat_id=? AND round=?", (bot_id, chat_id, round_))
    await db.commit()


async def bump_attempts(db: aiosqlite.Connection, bot_id: int, chat_id: int, round_: int) -> int:
    await db.execute("UPDATE announce_queue SET attempts=attempts+1 WHERE bot_id=? AND chat_id=? AND round=?",
                     (bot_id, chat_id, round_))
    await db.commit()
    async with db.execute("SELECT attempts FROM announce_queue WHERE bot_id=? AND chat_id=? AND round=?",
                          (bot_id, chat_id, round_)) as cur:
        row = await cur.fetchone()
    return row["attempts"] if row else 0


async def save_winners(db: aiosqlite.Connection, bot_id: int, chat_id: int, round_: int, winners: list[dict]) -> None:
    await db.execute("DELETE FROM winners WHERE bot_id=? AND chat_id=? AND round=?", (bot_id, chat_id, round_))
    await db.executemany(
        "INSERT INTO winners(bot_id, chat_id, round, rank, user_id, name, messages, prize) VALUES(?,?,?,?,?,?,?,?)",
        [(bot_id, chat_id, round_, w["rank"], w["user_id"], w["name"], w["messages"], w["prize"]) for w in winners],
    )
    await db.commit()


def prize_list(bot: aiosqlite.Row) -> list[str]:
    return [p.strip() for p in (bot["prizes"] or "").splitlines() if p.strip()]


def pick_winners(rows: list, prizes: list[str], qualify: int) -> list[dict]:
    """경품이 있을 때만 수상자가 있다. 응모 자격 횟수 이상인 사람 중 상위 순서대로 경품 개수만큼."""
    eligible = [r for r in rows if r["messages"] >= qualify]
    return [
        {"rank": i + 1, "user_id": r["user_id"], "name": r["name"], "messages": r["messages"], "prize": prizes[i]}
        for i, r in enumerate(eligible[: len(prizes)])
    ]
