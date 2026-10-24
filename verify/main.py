"""채널 가입 검수 봇.

필수 채널의 관리자로 들어가 있는 이 봇이 `getChatMember`로 가입 여부를 확인하고, 같은 서버의 다른 봇들은
127.0.0.1의 검수 서비스로 "이 유저가 가입했나?"만 묻는다. 새 봇을 만들어도 채널에 추가할 필요가 없다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "shared"))   # 통합 폴더

from bot.handlers import router
from bot.texts import TEXTS
from core import run_bot
from core.verifier import start_server


async def on_startup(bot, db, cfg):
    port = int(cfg.env.get("VERIFY_PORT") or 8765)
    runner = await start_server(bot, cfg.common.channels, cfg.env.get("VERIFY_SECRET", ""), port)
    return {"verify_runner": runner}


if __name__ == "__main__":
    # 이 봇은 자기 자신에게 가입 게이트를 걸지 않는다 (gate=False)
    run_bot(ROOT, routers=[router], texts=TEXTS, on_startup=on_startup, gate=False)
