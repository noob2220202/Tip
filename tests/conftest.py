from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import BotConfig, Channel, CommonConfig  # noqa: E402
from core.db import open_db  # noqa: E402
from core.texts import Texts  # noqa: E402
from tests.fake_api import FakeTelegram  # noqa: E402

ADMIN = 900
TOKEN = "123456:" + "A" * 35


@pytest.fixture
async def tg():
    t = FakeTelegram()
    await t.start()
    yield t
    await t.stop()


@pytest.fixture
async def bot(tg):
    b = Bot(TOKEN, session=AiohttpSession(api=TelegramAPIServer.from_base(tg.base)),
            default=DefaultBotProperties(parse_mode="HTML"))
    yield b
    await b.session.close()


@pytest.fixture
def make_cfg(tmp_path):
    def _make(channels=(), fail_mode="allow", rate=1000, exempt=True) -> BotConfig:
        d = tmp_path / "bot"
        (d / "data").mkdir(parents=True, exist_ok=True)
        return BotConfig(
            name="t", bot_dir=d, token=TOKEN, admin_ids=frozenset({ADMIN}), db_path=d / "data" / "bot.db",
            common=CommonConfig(channels=tuple(channels), cache_ttl=60, fail_mode=fail_mode,
                                exempt_admins=exempt, rate_per_sec=rate),
        )
    return _make


@pytest.fixture
async def db(make_cfg):
    cfg = make_cfg()
    conn = await open_db(cfg.db_path)
    yield conn
    await conn.close()


_uid = [1000]


def msg_update(user_id: int, text: str | None = None, **extra) -> Update:
    _uid[0] += 1
    m = {"message_id": _uid[0], "date": 0, "chat": {"id": user_id, "type": "private"},
         "from": {"id": user_id, "is_bot": False, "first_name": "테스터", "username": f"u{user_id}"}}
    if text is not None:
        m["text"] = text
        if text.startswith("/"):
            m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    m.update(extra)
    return Update.model_validate({"update_id": _uid[0], "message": m})


def cb_update(user_id: int, data: str) -> Update:
    _uid[0] += 1
    return Update.model_validate({"update_id": _uid[0], "callback_query": {
        "id": str(_uid[0]), "chat_instance": "x", "data": data,
        "from": {"id": user_id, "is_bot": False, "first_name": "테스터"},
        "message": {"message_id": 5, "date": 0, "chat": {"id": user_id, "type": "private"},
                    "from": {"id": 1, "is_bot": True, "first_name": "bot"}, "text": "gate"}}})
