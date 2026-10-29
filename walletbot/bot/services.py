"""봇이 켜질 때 만드는 객체들과 감시 루프."""
from __future__ import annotations

import asyncio
import logging
import time

from bot.chain import RateLimited, TronError, TronReader
from bot.energy import Desk
from bot.notify import Notifier
from bot.prices import Prices
from bot.scanner import Scanner
from bot.suppliers import CatFee, Netts, Supplier, Trxx, trx_to_sun
from bot.watcher import Watcher
from core.config import BotConfig
from core.tron import is_tron_address

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


async def _energy_loop(desk: Desk) -> None:
    await desk.recover()
    while True:
        wait = 10.0
        try:
            wait = await desk.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("에너지 처리 루프 오류")
        desk.wake.clear()
        try:
            await asyncio.wait_for(desk.wake.wait(), timeout=wait)    # 입금이 확인되면 기다리지 않고 바로 처리한다
        except asyncio.TimeoutError:
            pass


def build_suppliers(env) -> list[Supplier]:
    """키가 들어 있는 공급처만 쓴다."""
    out: list[Supplier] = []
    if env.get("NETTS_API_KEY"):
        out.append(Netts(env["NETTS_API_KEY"], env.get("NETTS_REAL_IP", ""), env.get("NETTS_URL") or "https://netts.io/apiv2"))
    if env.get("CATFEE_API_KEY") and env.get("CATFEE_API_SECRET"):
        out.append(CatFee(env["CATFEE_API_KEY"], env["CATFEE_API_SECRET"], env.get("CATFEE_URL") or "https://api.catfee.io"))
    if env.get("TRXX_API_KEY") and env.get("TRXX_API_SECRET"):
        out.append(Trxx(env["TRXX_API_KEY"], env["TRXX_API_SECRET"], env.get("TRXX_URL") or "https://trxx.io"))
    return out


def build_desk(db, chain: TronReader, notifier: Notifier, env) -> Desk:
    pay = (env.get("ENERGY_PAY_ADDRESS") or "").strip()
    if pay and not is_tron_address(pay):
        log.error("ENERGY_PAY_ADDRESS가 올바른 TRON 주소가 아닙니다. 에너지 임대를 끕니다.")
        pay = ""
    return Desk(db, chain, notifier, build_suppliers(env), pay,
                margin=trx_to_sun(env.get("ENERGY_MARGIN_TRX") or 0),
                max_loss=trx_to_sun(env.get("ENERGY_MAX_LOSS_TRX") or 1),
                ttl=max(5, int(env.get("ENERGY_ORDER_MINUTES") or 10)) * 60,
                low_balance=trx_to_sun(env.get("ENERGY_LOW_BALANCE_TRX") or 50))


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    chain = TronReader(cfg.env.get("TRONGRID_URL") or "https://api.trongrid.io", cfg.env.get("TRONGRID_API_KEY", ""))
    prices = Prices(cfg.env.get("BITHUMB_URL") or "https://api.bithumb.com", cfg.env.get("FX_URL") or "https://open.er-api.com")
    notifier = Notifier(bot, db, cfg.admin_ids)
    minutes = max(5, int(cfg.env.get("RECONCILE_MINUTES") or DEFAULT_RECONCILE_MIN))
    watcher = Watcher(db, chain, prices, notifier, reconcile_seconds=minutes * 60)
    desk = build_desk(db, chain, notifier, cfg.env)
    scanner = Scanner(db, chain, watcher, desk)
    tasks = [asyncio.create_task(_watch_loop(watcher), name="walletbot-watch"),
             asyncio.create_task(_scan_loop(scanner), name="walletbot-scan"),
             asyncio.create_task(_energy_loop(desk), name="walletbot-energy")]
    if desk.enabled:
        log.info("에너지 임대 켜짐: 공급처 %s, 결제 주소 %s", ", ".join(desk.suppliers), desk.pay_address)
    else:
        log.info("에너지 임대 꺼짐 (ENERGY_PAY_ADDRESS와 공급처 키가 필요합니다)")
    return {"chain": chain, "prices": prices, "notifier": notifier, "watcher": watcher, "scanner": scanner,
            "energy": desk, "bg_tasks": tasks,
            "max_wallets_total": int(cfg.env.get("MAX_WALLETS_TOTAL") or DEFAULT_MAX_WALLETS)}


async def on_shutdown(extra) -> None:
    for t in extra["bg_tasks"]:
        t.cancel()
    await asyncio.gather(*extra["bg_tasks"], return_exceptions=True)
    await extra["chain"].close()
    await extra["prices"].close()
    await extra["energy"].close()
