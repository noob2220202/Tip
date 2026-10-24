"""aiosqlite 연결(WAL, busy_timeout)과 스키마 마이그레이션."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT,
  started_at  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL,
  is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS campaigns (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT NOT NULL,
  content_type  TEXT NOT NULL,
  text          TEXT,
  file_id       TEXT,
  buttons_json  TEXT,
  status        TEXT NOT NULL,
  target_count  INTEGER NOT NULL DEFAULT 0,
  created_by    INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  started_at    INTEGER,
  finished_at   INTEGER
);

CREATE TABLE IF NOT EXISTS campaign_recipients (
  campaign_id  INTEGER NOT NULL REFERENCES campaigns(id),
  user_id      INTEGER NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  error        TEXT,
  sent_at      INTEGER,
  PRIMARY KEY (campaign_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_recip_pending ON campaign_recipients(campaign_id, status);
"""


async def open_db(path: Path | str, extra_schema: str = "") -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(CORE_SCHEMA)
    if extra_schema:
        await db.executescript(extra_schema)
    await db.commit()
    return db
