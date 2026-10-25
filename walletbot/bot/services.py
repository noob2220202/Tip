"""봇이 켜질 때 만드는 객체들과 감시 루프."""
from __future__ import annotations

import asyncio
import logging

from bot.chain import TronReader
from bot.notify import Notifier
from bot.prices import Prices
from bot.watcher import Watcher
from core.config import BotConfig

log = logging.getLogger(__name__)

DEFAULT_MAX_WALLETS = 30
DEFAULT_POLL = 60
TICK_SECONDS = 15


async def _loop(watcher: Watcher) -> None:
    while True:
        try:
            await watcher.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 한 번 실패해도 감시 루프는 계속 돌아야 한다
            log.exception("감시 루프 오류")
        await asyncio.sleep(TICK_SECONDS)


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    chain = TronReader(cfg.env.get("TRONGRID_URL") or "https://api.trongrid.io", cfg.env.get("TRONGRID_API_KEY", ""))
    prices = Prices(cfg.env.get("BITHUMB_URL") or "https://api.bithumb.com", cfg.env.get("FX_URL") or "https://open.er-api.com")
    notifier = Notifier(bot, db, cfg.admin_ids)
    poll = max(15, int(cfg.env.get("POLL_SECONDS") or DEFAULT_POLL))
    watcher = Watcher(db, chain, prices, notifier, poll_seconds=poll)
    task = asyncio.create_task(_loop(watcher), name="walletbot-watcher")
    return {"chain": chain, "prices": prices, "notifier": notifier, "watcher": watcher, "watch_task": task,
            "max_wallets_total": int(cfg.env.get("MAX_WALLETS_TOTAL") or DEFAULT_MAX_WALLETS)}


async def on_shutdown(extra) -> None:
    extra["watch_task"].cancel()
    await asyncio.gather(extra["watch_task"], return_exceptions=True)
    await extra["chain"].close()
    await extra["prices"].close()
