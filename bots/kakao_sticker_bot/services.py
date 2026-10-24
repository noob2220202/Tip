"""봇 시작 시 만들어 핸들러에 주입하는 객체들."""
from __future__ import annotations

import asyncio
from pathlib import Path

from bots.kakao_sticker_bot.kakao import KakaoClient, Session
from core.config import BotConfig

DEFAULT_EMOJI = "😊"
CONVERT_CONCURRENCY = 2


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    session_path = Path(cfg.env.get("KAKAO_SESSION_PATH") or cfg.bot_dir / "data" / "session.json")
    session = Session(session_path)
    return {
        "kakao": KakaoClient(session),
        "kakao_session": session,
        "convert_sem": asyncio.Semaphore(CONVERT_CONCURRENCY),
        "inflight": set(),
        "emoji": cfg.env.get("STICKER_EMOJI") or DEFAULT_EMOJI,
        "alert_state": {"last": 0.0},
    }
