"""DB 스키마와 질의. 기프트콘 코드는 한 번 쓰면 끝이라, 배정 상태를 DB에서 먼저 바꾸고 나서 보낸다."""
from __future__ import annotations

import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kind         TEXT NOT NULL,                 -- draw(추첨) | all(전원 동일)
  title        TEXT NOT NULL,
  winners      INTEGER NOT NULL DEFAULT 0,    -- 추첨 인원
  note         TEXT NOT NULL DEFAULT '',      -- 당첨자에게 덧붙일 안내
  status       TEXT NOT NULL DEFAULT 'draft', -- draft | running | paused | done | cancelled
  created_by   INTEGER NOT NULL,
  created_at   INTEGER NOT NULL,
  started_at   INTEGER,
  finished_at  INTEGER,
  stop_reason  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS items (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id    INTEGER NOT NULL,
  kind        TEXT NOT NULL,                  -- text(코드) | photo(이미지, 텔레그램 file_id)
  content     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'free',   -- free | assigned | sent | uncertain
  user_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_items_event ON items(event_id, status);

-- 이벤트에서 이미 시도한 사람. 재시작해도 같은 사람을 다시 뽑지 않는다
CREATE TABLE IF NOT EXISTS tries (
  event_id  INTEGER NOT NULL,
  user_id   INTEGER NOT NULL,
  result    TEXT NOT NULL,                    -- sending | won | unsub | blocked | uncertain
  item_id   INTEGER,
  at        INTEGER NOT NULL,
  PRIMARY KEY (event_id, user_id)
);
"""

MAX_ITEMS = 500


# ---- 이벤트 ----

async def create_event(db: aiosqlite.Connection, kind: str, title: str, winners: int, note: str, created_by: int,
                       now: float | None = None) -> int:
    cur = await db.execute(
        "INSERT INTO events(kind, title, winners, note, created_by, created_at) VALUES(?,?,?,?,?,?)",
        (kind, title, winners, note, created_by, int(now or time.time())),
    )
    await db.commit()
    return cur.lastrowid


async def get_event(db: aiosqlite.Connection, event_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM events WHERE id=?", (event_id,)) as cur:
        return await cur.fetchone()


async def list_events(db: aiosqlite.Connection, limit: int = 10) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)) as cur:
        return list(await cur.fetchall())


async def running_events(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM events WHERE status='running'") as cur:
        return list(await cur.fetchall())


async def transition(db: aiosqlite.Connection, event_id: int, frm: tuple[str, ...], to: str, reason: str = "",
                     now: float | None = None) -> bool:
    """상태를 바꾼다. 지금 상태가 frm 중 하나일 때만 바뀌고, 아니면 False (같은 이벤트를 두 번 실행하지 않게)."""
    marks = ",".join("?" * len(frm))
    sets = "status=?, stop_reason=?"
    args: list = [to, reason]
    if to == "running":
        sets += ", started_at=COALESCE(started_at, ?)"
        args.append(int(now or time.time()))
    if to in ("done", "cancelled"):
        sets += ", finished_at=?"
        args.append(int(now or time.time()))
    cur = await db.execute(f"UPDATE events SET {sets} WHERE id=? AND status IN ({marks})", (*args, event_id, *frm))
    await db.commit()
    return cur.rowcount == 1


# ---- 기프트콘 ----

async def add_items(db: aiosqlite.Connection, event_id: int, items: list[tuple[str, str]]) -> None:
    await db.executemany("INSERT INTO items(event_id, kind, content) VALUES(?,?,?)",
                         [(event_id, k, c) for k, c in items])
    await db.commit()


async def item_counts(db: aiosqlite.Connection, event_id: int) -> dict[str, int]:
    out = {"free": 0, "assigned": 0, "sent": 0, "uncertain": 0}
    async with db.execute("SELECT status, COUNT(*) AS n FROM items WHERE event_id=? GROUP BY status", (event_id,)) as cur:
        for r in await cur.fetchall():
            out[r["status"]] = r["n"]
    out["total"] = sum(out.values())
    return out


async def first_item(db: aiosqlite.Connection, event_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM items WHERE event_id=? ORDER BY id LIMIT 1", (event_id,)) as cur:
        return await cur.fetchone()


async def claim_item(db: aiosqlite.Connection, event_id: int, user_id: int) -> aiosqlite.Row | None:
    """남은 코드 하나를 이 사용자에게 배정한다. 배정을 DB에 먼저 남기므로 같은 코드가 두 사람에게 갈 수 없다."""
    async with db.execute("SELECT id FROM items WHERE event_id=? AND status='free' ORDER BY id LIMIT 1", (event_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    cur = await db.execute("UPDATE items SET status='assigned', user_id=? WHERE id=? AND status='free'", (user_id, row["id"]))
    await db.commit()
    if cur.rowcount != 1:
        return None
    async with db.execute("SELECT * FROM items WHERE id=?", (row["id"],)) as cur2:
        return await cur2.fetchone()


async def release_item(db: aiosqlite.Connection, item_id: int) -> None:
    """확실히 못 보낸 경우에만 코드를 되돌린다."""
    await db.execute("UPDATE items SET status='free', user_id=NULL WHERE id=? AND status='assigned'", (item_id,))
    await db.commit()


async def set_item(db: aiosqlite.Connection, item_id: int, status: str) -> None:
    await db.execute("UPDATE items SET status=? WHERE id=?", (status, item_id))
    await db.commit()


async def wipe_codes(db: aiosqlite.Connection, event_id: int) -> int:
    """끝난 이벤트의 코드·이미지 값을 지운다(누가 받았는지 기록은 남긴다)."""
    cur = await db.execute("UPDATE items SET content='' WHERE event_id=? AND content<>''", (event_id,))
    await db.commit()
    return cur.rowcount


# ---- 시도 기록 ----

async def set_try(db: aiosqlite.Connection, event_id: int, user_id: int, result: str, item_id: int | None = None,
                  now: float | None = None) -> None:
    await db.execute("INSERT OR REPLACE INTO tries(event_id, user_id, result, item_id, at) VALUES(?,?,?,?,?)",
                     (event_id, user_id, result, item_id, int(now or time.time())))
    await db.commit()


async def delete_try(db: aiosqlite.Connection, event_id: int, user_id: int) -> None:
    await db.execute("DELETE FROM tries WHERE event_id=? AND user_id=?", (event_id, user_id))
    await db.commit()


async def try_counts(db: aiosqlite.Connection, event_id: int) -> dict[str, int]:
    out = {"won": 0, "unsub": 0, "blocked": 0, "uncertain": 0, "sending": 0}
    async with db.execute("SELECT result, COUNT(*) AS n FROM tries WHERE event_id=? GROUP BY result", (event_id,)) as cur:
        for r in await cur.fetchall():
            out[r["result"]] = r["n"]
    return out


async def candidates(db: aiosqlite.Connection, event_id: int, admin_ids, randomize: bool, limit: int = 1) -> list[int]:
    """아직 시도하지 않은 활성 사용자(관리자 제외). 추첨은 무작위로, 전원 발송은 순서대로."""
    admins = list(admin_ids)
    marks = ",".join("?" * len(admins)) or "NULL"
    order = "RANDOM()" if randomize else "u.user_id"
    async with db.execute(
        f"SELECT u.user_id FROM users u WHERE u.is_active=1 AND u.user_id NOT IN ({marks})"
        " AND NOT EXISTS (SELECT 1 FROM tries t WHERE t.event_id=? AND t.user_id=u.user_id)"
        f" ORDER BY {order} LIMIT ?",
        (*admins, event_id, limit),
    ) as cur:
        return [r["user_id"] for r in await cur.fetchall()]


async def pool_size(db: aiosqlite.Connection, event_id: int, admin_ids) -> int:
    admins = list(admin_ids)
    marks = ",".join("?" * len(admins)) or "NULL"
    async with db.execute(
        f"SELECT COUNT(*) FROM users u WHERE u.is_active=1 AND u.user_id NOT IN ({marks})"
        " AND NOT EXISTS (SELECT 1 FROM tries t WHERE t.event_id=? AND t.user_id=u.user_id)",
        (*admins, event_id),
    ) as cur:
        return (await cur.fetchone())[0]


async def winners_of(db: aiosqlite.Connection, event_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT t.user_id AS user_id, u.username AS username, t.at AS at FROM tries t"
        " LEFT JOIN users u ON u.user_id=t.user_id WHERE t.event_id=? AND t.result='won' ORDER BY t.at, t.user_id",
        (event_id,),
    ) as cur:
        return list(await cur.fetchall())


async def recover(db: aiosqlite.Connection) -> int:
    """재시작할 때: 보내던 도중 멈춘 건은 보내졌는지 알 수 없으므로 '불확실'로 돌린다(코드를 다시 쓰지 않는다)."""
    cur1 = await db.execute("UPDATE items SET status='uncertain' WHERE status='assigned'")
    cur2 = await db.execute("UPDATE tries SET result='uncertain' WHERE result='sending'")
    await db.commit()
    return max(cur1.rowcount, cur2.rowcount)
