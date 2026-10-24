from pathlib import Path

from bots.kakao_sticker_bot.handlers import router
from bots.kakao_sticker_bot.services import on_startup
from bots.kakao_sticker_bot.texts import TEXTS
from core import run_bot

SCHEMA = """
CREATE TABLE IF NOT EXISTS sticker_sets (
  user_id     INTEGER NOT NULL,
  key         TEXT NOT NULL,
  set_name    TEXT NOT NULL,
  title       TEXT NOT NULL,
  fmt         TEXT NOT NULL,
  total       INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL,            -- creating | complete
  created_at  INTEGER NOT NULL,
  PRIMARY KEY (user_id, key)
);
"""

if __name__ == "__main__":
    run_bot(Path(__file__).parent, routers=[router], texts=TEXTS, schema=SCHEMA, on_startup=on_startup)
