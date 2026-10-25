import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 통합 폴더(shared/)의 core를 불러온다. 서버에서는 /root/giveaway가 Tip/giveaway 링크여도 실제 위치(resolve) 기준으로 찾는다.
sys.path.insert(0, str(ROOT.parent / "shared"))

from bot.handlers import router
from bot.services import on_shutdown, on_startup
from bot.store import SCHEMA
from bot.texts import TEXTS
from core import run_bot

if __name__ == "__main__":
    run_bot(ROOT, routers=[router], texts=TEXTS, schema=SCHEMA, on_startup=on_startup, on_shutdown=on_shutdown)
