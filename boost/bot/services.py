"""봇 시작 시 만들어 핸들러에 주입하는 객체들과 백그라운드 작업."""
from __future__ import annotations

import asyncio
import logging

from bot import fulfil
from bot.addresses import is_tron_address
from bot.catalog import load_catalog
from bot.notify import Notifier
from bot.smm import SmmPanel
from bot.texts import TEXTS
from bot.tron import TronGrid
from bot.worker import Worker
from core.config import BotConfig
from core.texts import Texts

log = logging.getLogger(__name__)

DEFAULT_TTL_MIN = 30
DEFAULT_POLL = 15


def _need(cfg: BotConfig, key: str) -> str:
    v = (cfg.env.get(key) or "").strip()
    if not v:
        raise RuntimeError(f"{cfg.bot_dir / '.env'}에 {key}가 없어요.")
    return v


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    wallet = _need(cfg, "WALLET_ADDRESS")
    if not is_tron_address(wallet):
        raise RuntimeError("WALLET_ADDRESS가 올바른 TRON 주소가 아니에요.")
    panel = SmmPanel(_need(cfg, "SMM_API_URL"), _need(cfg, "SMM_API_KEY"))
    tron = TronGrid(cfg.env.get("TRONGRID_URL") or "https://api.trongrid.io", cfg.env.get("TRONGRID_API_KEY", ""), wallet)
    catalog = load_catalog()
    ttl_min = int(cfg.env.get("ORDER_TTL_MIN") or DEFAULT_TTL_MIN)

    recovered = await fulfil.recover(db)
    if recovered:
        log.warning("패널 접수 중이던 주문 %d건을 확인 필요로 돌렸습니다", recovered)

    notifier = Notifier(bot, Texts(TEXTS), cfg.admin_ids)
    worker = Worker(db, tron, panel, notifier.event, notifier.alert,
                    poll_seconds=int(cfg.env.get("POLL_SECONDS") or DEFAULT_POLL))
    # 입금 조회·패널 접수·진행 확인 루프를 띄운다 (테스트는 이 함수를 거치지 않고 worker.tick()을 직접 부른다)
    task = asyncio.create_task(worker.run(), name="boost-worker")
    return {
        "catalog": catalog, "wallet": wallet, "ttl_min": ttl_min, "panel": panel, "tron": tron,
        "worker": worker, "notifier": notifier, "worker_task": task,
    }
