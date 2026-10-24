"""캠페인/수신자 DB 접근."""
from __future__ import annotations

import json
import time

import aiosqlite


async def create_campaign(
    db: aiosqlite.Connection,
    *,
    title: str,
    content_type: str,
    text: str | None,
    file_id: str | None,
    buttons: list[dict],
    created_by: int,
) -> int:
    cur = await db.execute(
        """INSERT INTO campaigns (title, content_type, text, file_id, buttons_json, status, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)""",
        (title, content_type, text, file_id, json.dumps(buttons, ensure_ascii=False), created_by, int(time.time())),
    )
    await db.commit()
    return cur.lastrowid


async def get_campaign(db: aiosqlite.Connection, campaign_id: int) -> aiosqlite.Row | None:
    async with db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)) as cur:
        return await cur.fetchone()


async def list_campaigns(db: aiosqlite.Connection, limit: int = 15) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT ?", (limit,)) as cur:
        return list(await cur.fetchall())


async def count_active_users(db: aiosqlite.Connection) -> int:
    async with db.execute("SELECT COUNT(*) FROM users WHERE is_active=1") as cur:
        return (await cur.fetchone())[0]


async def start_campaign(db: aiosqlite.Connection, campaign_id: int) -> int:
    """draft → sending. 활성 사용자를 수신자로 스냅샷한다. 같은 캠페인은 한 번만 시작된다."""
    cur = await db.execute(
        "UPDATE campaigns SET status='sending', started_at=? WHERE id=? AND status='draft'",
        (int(time.time()), campaign_id),
    )
    if cur.rowcount == 0:
        await db.commit()
        return -1
    await db.execute(
        """INSERT OR IGNORE INTO campaign_recipients (campaign_id, user_id)
           SELECT ?, user_id FROM users WHERE is_active=1""",
        (campaign_id,),
    )
    async with db.execute("SELECT COUNT(*) FROM campaign_recipients WHERE campaign_id=?", (campaign_id,)) as c2:
        n = (await c2.fetchone())[0]
    await db.execute("UPDATE campaigns SET target_count=? WHERE id=?", (n, campaign_id))
    await db.commit()
    return n


async def set_status(db: aiosqlite.Connection, campaign_id: int, status: str) -> None:
    finished = int(time.time()) if status in ("done", "cancelled") else None
    await db.execute(
        "UPDATE campaigns SET status=?, finished_at=COALESCE(?, finished_at) WHERE id=?",
        (status, finished, campaign_id),
    )
    await db.commit()


async def sending_campaign_ids(db: aiosqlite.Connection) -> list[int]:
    async with db.execute("SELECT id FROM campaigns WHERE status='sending' ORDER BY id") as cur:
        return [r[0] for r in await cur.fetchall()]


async def next_pending(db: aiosqlite.Connection, campaign_id: int, limit: int = 50) -> list[int]:
    async with db.execute(
        "SELECT user_id FROM campaign_recipients WHERE campaign_id=? AND status='pending' ORDER BY user_id LIMIT ?",
        (campaign_id, limit),
    ) as cur:
        return [r[0] for r in await cur.fetchall()]


async def mark_recipient(
    db: aiosqlite.Connection, campaign_id: int, user_id: int, status: str, error: str | None = None
) -> None:
    await db.execute(
        "UPDATE campaign_recipients SET status=?, error=?, sent_at=? WHERE campaign_id=? AND user_id=?",
        (status, error, int(time.time()), campaign_id, user_id),
    )
    await db.commit()


async def recipient_counts(db: aiosqlite.Connection, campaign_id: int) -> dict[str, int]:
    counts = {"pending": 0, "sent": 0, "blocked": 0, "failed": 0}
    async with db.execute(
        "SELECT status, COUNT(*) FROM campaign_recipients WHERE campaign_id=? GROUP BY status", (campaign_id,)
    ) as cur:
        for status, n in await cur.fetchall():
            counts[status] = n
    return counts
