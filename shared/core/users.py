"""사용자 등록/조회/비활성 처리/통계."""
from __future__ import annotations

import time

import aiosqlite


async def register(db: aiosqlite.Connection, user_id: int, username: str | None) -> None:
    """/start를 보낸 사용자를 등록한다. 이미 있으면 다시 활성으로 돌리고 last_seen만 갱신."""
    now = int(time.time())
    await db.execute(
        """INSERT INTO users (user_id, username, started_at, last_seen, is_active)
           VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(user_id) DO UPDATE SET
             username=excluded.username, last_seen=excluded.last_seen, is_active=1""",
        (user_id, username, now, now),
    )
    await db.commit()


async def touch(db: aiosqlite.Connection, user_id: int) -> None:
    """등록된 사용자의 last_seen만 갱신한다 (미등록 사용자는 만들지 않는다)."""
    await db.execute("UPDATE users SET last_seen=? WHERE user_id=?", (int(time.time()), user_id))
    await db.commit()


async def set_active(db: aiosqlite.Connection, user_id: int, active: bool) -> None:
    await db.execute("UPDATE users SET is_active=? WHERE user_id=?", (1 if active else 0, user_id))
    await db.commit()


async def stats(db: aiosqlite.Connection) -> dict[str, int]:
    now = int(time.time())
    day_start = now - now % 86400
    async with db.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(is_active), 0) AS active,
                  COALESCE(SUM(started_at >= ?), 0) AS new_today,
                  COALESCE(SUM(is_active = 1 AND last_seen >= ?), 0) AS active_7d
           FROM users""",
        (day_start, now - 7 * 86400),
    ) as cur:
        row = await cur.fetchone()
    return dict(row)
