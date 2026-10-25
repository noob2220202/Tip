"""봇이 켜질 때 만드는 객체들과 감시 루프."""
from __future__ import annotations

import asyncio
import logging
import time

from bot.chain import RateLimited, TronError, TronReader
from bot.notify import Notifier
from bot.prices import Prices
from bot.scanner import Scanner
from bot.watcher import Watcher
from core.config import BotConfig

log = logging.getLogger(__name__)

DEFAULT_MAX_WALLETS = 500
DEFAULT_RECONCILE_MIN = 30
TICK_SECONDS = 15
ERROR_WAIT = 3.0


async def _watch_loop(watcher: Watcher) -> None:
    while True:
        try:
            await watcher.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 한 번 실패해도 감시 루프는 계속 돌아야 한다
            log.exception("감시 루프 오류")
        await asyncio.sleep(TICK_SECONDS)


async def _scan_loop(scanner: Scanner) -> None:
    while True:
        wait = ERROR_WAIT
        try:
            wait = await scanner.step()
        except asyncio.CancelledError:
            raise
        except RateLimited:
            await scanner.watcher.rate_limited(time.time())
            wait = 30.0
        except TronError as e:
            log.warning("블록 스캔 실패: %s", e)
            await scanner.watcher.alert_admins("scan", f"⚠️ 블록 스캔이 실패하고 있습니다: {e}", time.time())
        except Exception:  # noqa: BLE001
            log.exception("블록 스캔 오류")
        await asyncio.sleep(wait)


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    chain = TronReader(cfg.env.get("TRONGRID_URL") or "https://api.trongrid.io", cfg.env.get("TRONGRID_API_KEY", ""))
    prices = Prices(cfg.env.get("BITHUMB_URL") or "https://api.bithumb.com", cfg.env.get("FX_URL") or "https://open.er-api.com")
    notifier = Notifier(bot, db, cfg.admin_ids)
    minutes = max(5, int(cfg.env.get("RECONCILE_MINUTES") or DEFAULT_RECONCILE_MIN))
    watcher = Watcher(db, chain, prices, notifier, reconcile_seconds=minutes * 60)
    scanner = Scanner(db, chain, watcher)
    tasks = [asyncio.create_task(_watch_loop(watcher), name="walletbot-watch"),
             asyncio.create_task(_scan_loop(scanner), name="walletbot-scan")]
    return {"chain": chain, "prices": prices, "notifier": notifier, "watcher": watcher, "scanner": scanner,
            "bg_tasks": tasks, "max_wallets_total": int(cfg.env.get("MAX_WALLETS_TOTAL") or DEFAULT_MAX_WALLETS)}


async def on_shutdown(extra) -> None:
    for t in extra["bg_tasks"]:
        t.cancel()
    await asyncio.gather(*extra["bg_tasks"], return_exceptions=True)
    await extra["chain"].close()
    await extra["prices"].close()
