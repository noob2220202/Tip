import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 통합 폴더(shared/)의 core를 불러온다. 서버에서는 /root/kakao가 Tip/kakao 링크여도 실제 위치(resolve) 기준으로 찾는다.
sys.path.insert(0, str(ROOT.parent / "shared"))

from bot.handlers import router
from bot.services import on_startup
from bot.texts import TEXTS
from core import run_bot

SCHEMA = """
CREATE TABLE IF NOT EXISTS sticker_sets (
  user_id     INTEGER NOT NULL,
  key         TEXT NOT NULL,
  set_name    TEXT NOT NULL,
  title       TEXT NOT NULL,
  fmt         TEXT NOT NULL,
  fingerprint TEXT,                     -- 이미지 목록의 해시. 링크 형태가 달라도 같은 이모티콘을 알아본다
  total       INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL,            -- creating | complete
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (user_id, key)
);
"""

if __name__ == "__main__":
    run_bot(ROOT, routers=[router], texts=TEXTS, schema=SCHEMA, on_startup=on_startup)
