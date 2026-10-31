import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 통합 폴더(shared/)의 core를 불러온다. 서버에서는 /root/walletbot이 Tip/walletbot 링크여도 실제 위치(resolve) 기준으로 찾는다.
sys.path.insert(0, str(ROOT.parent / "shared"))

from bot.energy import SCHEMA as ENERGY_SCHEMA
from bot.handlers import router
from bot.handlers.business import MEMORY
from bot.services import on_shutdown, on_startup
from bot.store import SCHEMA
from bot.texts import TEXTS
from core import run_bot

if __name__ == "__main__":
    # MEMORY: 비즈니스 대화의 마지막 지갑 주소 기록은 구독 확인보다 먼저 한다
    run_bot(ROOT, routers=[router], texts=TEXTS, schema=SCHEMA + ENERGY_SCHEMA, on_startup=on_startup,
            on_shutdown=on_shutdown, pre_gate=[MEMORY])
