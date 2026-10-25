"""봇이 켜질 때 만드는 객체들."""
from __future__ import annotations

import logging

from bot.eligibility import Checker
from bot.engine import Engine
from core.config import BotConfig

log = logging.getLogger(__name__)

DEFAULT_PER_SEC = 20


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    checker = Checker(cfg, bot)
    engine = Engine(bot, db, checker, cfg.admin_ids, per_sec=float(cfg.env.get("SEND_PER_SEC") or DEFAULT_PER_SEC))
    await engine.resume_running()
    return {"engine": engine, "checker": checker}


async def on_shutdown(extra) -> None:
    await extra["engine"].stop_all()
