"""봇들이 같이 쓰는 pytest 픽스처. 각 프로젝트의 conftest.py가 pytest_plugins로 불러온다."""
from __future__ import annotations

import io
import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Update

from core.config import BotConfig, Channel, CommonConfig  
from core.db import open_db  
from core.texts import Texts  
from testkit.fake_api import FakeTelegram  

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


def biz_conn(owner_id: int, conn_id: str = "bc1", enabled: bool = True, can_reply: bool = True) -> dict:
    """BusinessConnection 객체(dict). FakeTelegram.business에 넣거나 biz_conn_update로 보낸다."""
    return {"id": conn_id, "user": {"id": owner_id, "is_bot": False, "first_name": "주인"}, "user_chat_id": owner_id,
            "date": 0, "is_enabled": enabled, "rights": {"can_reply": True} if can_reply else {}}


def biz_conn_update(owner_id: int, conn_id: str = "bc1", enabled: bool = True, can_reply: bool = True) -> Update:
    _uid[0] += 1
    return Update.model_validate({"update_id": _uid[0], "business_connection": biz_conn(owner_id, conn_id, enabled, can_reply)})


def biz_update(sender_id: int, peer_id: int, text: str, conn_id: str = "bc1", reply_text: str | None = None,
               **extra) -> Update:
    """비즈니스 대화의 메시지. chat은 주인과 대화 중인 상대(peer)이고, 보낸 사람은 주인이거나 상대다."""
    _uid[0] += 1
    m = {"message_id": _uid[0], "date": 0, "chat": {"id": peer_id, "type": "private"}, "business_connection_id": conn_id,
         "from": {"id": sender_id, "is_bot": False, "first_name": "사람"}, "text": text}
    if reply_text is not None:
        m["reply_to_message"] = {"message_id": 1, "date": 0, "chat": {"id": peer_id, "type": "private"},
                                 "business_connection_id": conn_id, "text": reply_text,
                                 "from": {"id": peer_id, "is_bot": False, "first_name": "상대"}}
    m.update(extra)
    return Update.model_validate({"update_id": _uid[0], "business_message": m})
