"""봇 시작 시 만들어 핸들러에 주입하는 객체들."""
from __future__ import annotations

import asyncio
from pathlib import Path

from bot.kakao import KakaoClient, Session
from core.config import BotConfig

DEFAULT_EMOJI = "😊"
# 동시에 변환하는 이모티콘 수. 변환 하나가 메모리를 수백MB 쓰므로 램이 작으면 1로 둔다 (.env의 CONVERT_CONCURRENCY)
DEFAULT_CONCURRENCY = 1


async def migrate(db) -> None:
    """이미 만들어진 DB에 새 컬럼·인덱스를 더한다."""
    async with db.execute("PRAGMA table_info(sticker_sets)") as cur:
        cols = {r["name"] for r in await cur.fetchall()}
    if "fingerprint" not in cols:
        await db.execute("ALTER TABLE sticker_sets ADD COLUMN fingerprint TEXT")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_sets_key ON sticker_sets(key, status)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_sets_fp ON sticker_sets(fingerprint, status)")
    await db.commit()


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    await migrate(db)
    session_path = Path(cfg.env.get("KAKAO_SESSION_PATH") or cfg.bot_dir / "data" / "session.json")
    session = Session(session_path)
    return {
        "kakao": KakaoClient(session),
        "kakao_session": session,
        "convert_sem": asyncio.Semaphore(max(1, int(cfg.env.get("CONVERT_CONCURRENCY") or DEFAULT_CONCURRENCY))),
        "inflight": set(),
        "emoji": cfg.env.get("STICKER_EMOJI") or DEFAULT_EMOJI,
        "alert_state": {"last": 0.0},
    }
