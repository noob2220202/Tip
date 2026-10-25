"""관리 봇이 켜질 때 고객 봇 묶음(Fleet)과 주간 발표 루프를 만들어 핸들러에 건네준다."""
from __future__ import annotations

import asyncio
import logging

from bot import texts as T
from bot.crypto import TokenBox
from bot.fleet import Fleet
from bot.settler import Settler
from core.config import BotConfig

log = logging.getLogger(__name__)

DEFAULT_MAX_BOTS = 3


async def _loop(settler: Settler) -> None:
    while True:
        try:
            await settler.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 한 번 실패해도 발표 루프는 계속 돌아야 한다
            log.exception("발표 루프 오류")
        await asyncio.sleep(settler.tick_seconds)


async def on_startup(bot, db, cfg: BotConfig) -> dict:
    key = (cfg.env.get("TOKEN_KEY") or "").strip()
    if not key:
        raise RuntimeError(f"{cfg.bot_dir / '.env'}에 TOKEN_KEY가 없어요. .env.example의 만드는 방법을 보세요.")
    box = TokenBox(key)
    me = await bot.get_me()

    async def revoked(owner_id: int, username: str) -> None:
        try:
            await bot.send_message(owner_id, T.REVOKED.format(username=username))
        except Exception:  # noqa: BLE001 - 주인이 관리 봇을 막았어도 넘어간다
            log.warning("토큰 폐기 알림을 보내지 못했습니다 owner=%s", owner_id)

    fleet = Fleet(db, box, api_base=cfg.api_base, manager_username=me.username or "", on_revoked=revoked)
    await fleet.start_all()
    settler = Settler(db, fleet)
    task = asyncio.create_task(_loop(settler), name="rankbot-settler")
    log.info("고객 봇 %d개를 시작했습니다", len(fleet.bots))
    return {"fleet": fleet, "box": box, "settler": settler, "settler_task": task,
            "max_bots": int(cfg.env.get("MAX_BOTS_PER_USER") or DEFAULT_MAX_BOTS)}


async def on_shutdown(extra) -> None:
    extra["settler_task"].cancel()
    await asyncio.gather(extra["settler_task"], return_exceptions=True)
    await extra["fleet"].stop_all()
