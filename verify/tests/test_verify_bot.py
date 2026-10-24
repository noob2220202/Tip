import dataclasses
import json

from bot.handlers import router
from bot.texts import TEXTS
from core.bot_runner import assemble
from core.config import Channel
from core.texts import Texts
from testkit.fixtures import ADMIN, msg_update

CH = Channel(chat_id="@chan", url="https://t.me/chan", title="채널")


async def make(make_cfg, bot, db):
    router._parent_router = None
    cfg = make_cfg(channels=[CH])
    cfg = dataclasses.replace(cfg, common=dataclasses.replace(cfg.common, verifier_url="http://127.0.0.1:1"))
    dp, _ = await assemble(cfg, bot, db, [router], Texts(TEXTS), gate_enabled=False)   # 검수 봇은 게이트 없음
    return dp


async def test_start_has_no_gate_and_registers(make_cfg, bot, tg, db):
    dp = await make(make_cfg, bot, db)
    await dp.feed_update(bot, msg_update(5, "/start"))
    assert "검수 전용" in tg.texts_to(5)[-1]
    assert not tg.of("getChatMember")                               # 자기 자신에게 게이트를 걸지 않는다


async def test_admin_commands(make_cfg, bot, tg, db):
    dp = await make(make_cfg, bot, db)
    await dp.feed_update(bot, msg_update(ADMIN, "/channels"))
    assert "문제가 발견" in tg.texts_to(ADMIN)[-1]                   # 봇이 채널 관리자가 아님
    tg.members[("@chan", 1)] = "administrator"
    await dp.feed_update(bot, msg_update(ADMIN, "/channels"))
    assert "정상입니다" in tg.texts_to(ADMIN)[-1]

    await dp.feed_update(bot, msg_update(ADMIN, "/check 77"))
    assert "미가입" in tg.texts_to(ADMIN)[-1]
    tg.members[("@chan", 77)] = "member"
    await dp.feed_update(bot, msg_update(ADMIN, "/check 77"))
    assert "✅ 가입" in tg.texts_to(ADMIN)[-1]


async def test_admin_commands_are_silent_for_others(make_cfg, bot, tg, db):
    dp = await make(make_cfg, bot, db)
    await dp.feed_update(bot, msg_update(6, "/check 1"))
    await dp.feed_update(bot, msg_update(6, "/channels"))
    assert tg.texts_to(6) == []


def test_ecosystem_config():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(["node", "-e", "console.log(JSON.stringify(require('./ecosystem.config.js').apps))"],
                         cwd=root, capture_output=True, text=True)
    apps = json.loads(out.stdout)
    assert [a["name"] for a in apps] == ["verify"] and (root / apps[0]["script"]).exists()
