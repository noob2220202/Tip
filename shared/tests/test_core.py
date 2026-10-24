import json
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, Update

from core import users
from core.bot_runner import assemble
from core.broadcast import models
from core.broadcast.sender import CampaignSender
from core.config import Channel
from core.logging_setup import TokenMaskFilter
from core.texts import Texts
from testkit.fixtures import ADMIN, cb_update, msg_update

CH = Channel(chat_id="@chan", url="https://t.me/chan", title="채널")
ROOT = Path(__file__).resolve().parent.parent


EXAMPLE_TEXTS = {"pong": ["퐁"]}
example_router = Router(name="test_ping")


@example_router.message(Command("ping"))
async def _ping(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("pong"))


async def build(cfg, bot, db):
    example_router._parent_router = None  # 모듈 전역 라우터를 테스트마다 새 Dispatcher에 붙인다
    dp, sender = await assemble(cfg, bot, db, [example_router], Texts(EXAMPLE_TEXTS))
    return dp, sender


async def feed(dp, bot, upd: Update):
    await dp.feed_update(bot, upd)


async def test_gate_blocks_then_passes_and_resumes(make_cfg, bot, tg, db):
    cfg = make_cfg(channels=[CH])
    dp, _ = await build(cfg, bot, db)
    await feed(dp, bot, msg_update(10, "/ping"))
    sent = tg.of("sendMessage")
    assert len(sent) == 1 and "가입했어요" in sent[0]["text"]
    kb = json.loads(sent[0]["reply_markup"])["inline_keyboard"]
    assert kb[0][0]["url"] == "https://t.me/chan" and kb[1][0]["callback_data"] == "gate:check"

    # 아직 미가입이면 확인 버튼은 경고만
    await feed(dp, bot, cb_update(10, "gate:check"))
    assert tg.of("answerCallbackQuery")[-1].get("show_alert") in ("true", True)
    assert len(tg.of("sendMessage")) == 1

    # 가입 후 확인 → 원래 요청(/ping)을 이어서 처리
    tg.members[("@chan", 10)] = "member"
    await feed(dp, bot, cb_update(10, "gate:check"))
    assert any(t.startswith("퐁") for t in tg.texts_to(10))
    assert tg.of("deleteMessage")


async def test_gate_registers_on_start_and_exempts_admin(make_cfg, bot, tg, db):
    cfg = make_cfg(channels=[CH])
    dp, _ = await build(cfg, bot, db)
    await feed(dp, bot, msg_update(11, "/start"))
    async with db.execute("SELECT is_active FROM users WHERE user_id=11") as c:
        assert (await c.fetchone())[0] == 1       # 막혔어도 등록은 됨
    # 일반 메시지는 미등록 사용자를 만들지 않는다
    await feed(dp, bot, msg_update(12, "hi"))
    async with db.execute("SELECT COUNT(*) FROM users WHERE user_id=12") as c:
        assert (await c.fetchone())[0] == 0
    # 관리자는 게이트 면제
    await feed(dp, bot, msg_update(ADMIN, "/ping"))
    assert any(t.startswith("퐁") for t in tg.texts_to(ADMIN))


async def test_gate_fail_modes(make_cfg, bot, db):
    from aiogram.exceptions import TelegramBadRequest

    from core.subscription import SubscriptionGate

    async def broken(chat_id, user_id, **k):
        raise TelegramBadRequest(method=None, message="chat not found")

    bot.get_chat_member = broken
    for mode, passes in (("allow", True), ("block", False)):
        gate = SubscriptionGate(make_cfg(channels=[CH], fail_mode=mode), db, Texts())
        assert (not await gate.missing(bot, 5)) is passes


async def test_stats_admin_only(make_cfg, bot, tg, db):
    cfg = make_cfg()
    dp, _ = await build(cfg, bot, db)
    await users.register(db, 1, "a")
    await users.register(db, 2, "b")
    await users.set_active(db, 2, False)
    await feed(dp, bot, msg_update(ADMIN, "/stats"))
    out = tg.texts_to(ADMIN)[-1]
    assert "<b>2</b>" in out and "활성 <b>1</b>" in out
    n = len(tg.calls)
    await feed(dp, bot, msg_update(55, "/stats"))   # 일반 사용자: 통계 응답 없음
    assert not any("<b>" in t for t in tg.texts_to(55))


async def _seed_users(db, ids):
    for i in ids:
        await users.register(db, i, f"u{i}")


async def test_broadcast_send_429_403_and_no_duplicates(make_cfg, bot, tg, db):
    cfg = make_cfg()
    await _seed_users(db, [1, 2, 3, 4])
    tg.forbidden.add(2)
    tg.retry_once.add(3)
    cid = await models.create_campaign(db, title="t", content_type="text", text="<b>광고</b>", file_id=None,
                                       buttons=[{"text": "열기", "url": "https://x.y"}], created_by=ADMIN)
    assert await models.start_campaign(db, cid) == 4
    assert await models.start_campaign(db, cid) == -1      # 두 번째 시작은 무시(중복 방지)
    sender = CampaignSender(bot, db, Texts(), rate_per_sec=1000)
    sender.start(cid)
    await sender.wait(cid)
    counts = await models.recipient_counts(db, cid)
    assert counts == {"pending": 0, "sent": 3, "blocked": 1, "failed": 0}
    async with db.execute("SELECT is_active FROM users WHERE user_id=2") as c:
        assert (await c.fetchone())[0] == 0
    to_3 = [p for p in tg.of("sendMessage") if p["chat_id"] == "3"]
    assert len(to_3) == 2                                   # 429 한 번 + 재시도 성공
    assert (await models.get_campaign(db, cid))["status"] == "done"
    assert "끝났어요" in tg.texts_to(ADMIN)[-1]


async def test_broadcast_resumes_after_restart(make_cfg, bot, tg, db):
    await _seed_users(db, [1, 2, 3])
    cid = await models.create_campaign(db, title="t", content_type="text", text="안녕", file_id=None,
                                       buttons=[], created_by=ADMIN)
    await models.start_campaign(db, cid)
    await models.mark_recipient(db, cid, 1, "sent")        # 재시작 전에 1명에게는 이미 나감
    sender = CampaignSender(bot, db, Texts(), rate_per_sec=1000)   # 새 프로세스를 흉내냄
    await sender.resume_all()
    await sender.wait(cid)
    targets = sorted(p["chat_id"] for p in tg.of("sendMessage") if p["text"] == "안녕")
    assert targets == ["2", "3"]                           # 1번에게는 다시 보내지 않는다
    assert (await models.recipient_counts(db, cid))["sent"] == 3


async def test_broadcast_flow_via_admin_chat(make_cfg, bot, tg, db):
    cfg = make_cfg()
    dp, sender = await build(cfg, bot, db)
    await _seed_users(db, [1, 2])
    await feed(dp, bot, msg_update(ADMIN, "/broadcast"))
    await feed(dp, bot, msg_update(ADMIN, "광고 본문", entities=[{"type": "bold", "offset": 0, "length": 2}]))
    await feed(dp, bot, msg_update(ADMIN, "깨진 버튼"))
    assert "형식이 안 맞아요" in tg.texts_to(ADMIN)[-1]
    await feed(dp, bot, msg_update(ADMIN, "열기 | https://example.com"))
    assert any("대상 <b>2</b>명" in t for t in tg.texts_to(ADMIN))
    assert any(p.get("text") == "<b>광고</b> 본문" for p in tg.of("sendMessage"))   # 서식 유지
    await feed(dp, bot, cb_update(ADMIN, "bc:send:1"))
    await feed(dp, bot, cb_update(ADMIN, "bc:send:1"))     # 두 번 눌러도 한 번만
    await sender.wait(1)
    got = [p for p in tg.of("sendMessage") if p["chat_id"] in ("1", "2")]
    assert len(got) == 2
    await feed(dp, bot, msg_update(ADMIN, "/adstats 1"))
    doc = tg.of("sendDocument")[-1]
    assert doc["caption"]
    assert any("전달률 <b>100.0%</b>" in t for t in tg.texts_to(ADMIN))


def test_token_mask():
    import logging
    rec = logging.LogRecord("x", logging.INFO, "", 0, "url https://api/bot123456:%s/getMe", ("A" * 35,), None)
    TokenMaskFilter().filter(rec)
    assert "AAAA" not in rec.getMessage() and "<token>" in rec.getMessage()


def test_repo_common_toml_requires_channel():
    from core.config import load_common

    c = load_common(ROOT / "config" / "common.toml")
    assert [(x.chat_id, x.url) for x in c.channels] == [("@GGULLTIP", "https://t.me/GGULLTIP")]
    assert c.fail_mode == "block"


async def test_self_check_reports_when_bot_is_not_channel_admin(make_cfg, bot, tg):
    from core.subscription import self_check

    cfg = make_cfg(channels=[CH])
    assert await self_check(bot, cfg) != []                       # 봇이 채널에 없음(left)
    tg.members[("@chan", 1)] = "member"                           # 일반 멤버여도 안 된다
    problems = await self_check(bot, cfg)
    assert len(problems) == 1 and "관리자가 아닙니다" in problems[0]
    tg.members[("@chan", 1)] = "administrator"
    assert await self_check(bot, cfg) == []
    assert await self_check(bot, make_cfg()) == []                # 채널 미설정이면 점검할 것이 없다


def test_repo_gate_blocks_on_failure():
    from core.config import load_common

    assert load_common(ROOT / "config" / "common.toml").fail_mode == "block"


async def test_drain_waits_for_inflight_update_and_gives_up_after_timeout(make_cfg, bot, db):
    import asyncio

    from core.bot_runner import drain

    cfg = make_cfg()
    router = Router(name="slow")
    done = []

    @router.message(Command("slow"))
    async def slow(message: Message) -> None:
        await asyncio.sleep(0.4)
        done.append("finished")

    dp, _ = await assemble(cfg, bot, db, [router], Texts())
    job = asyncio.create_task(dp.feed_update(bot, msg_update(1, "/slow")))
    await asyncio.sleep(0.1)
    assert await drain(dp["tracker"], timeout=5) == 0               # 끝날 때까지 기다린다
    assert done == ["finished"]
    await job

    router2 = Router(name="stuck")

    @router2.message(Command("stuck"))
    async def stuck(message: Message) -> None:
        await asyncio.sleep(30)

    cfg2 = make_cfg()
    dp2, _ = await assemble(cfg2, bot, db, [router2], Texts())
    job2 = asyncio.create_task(dp2.feed_update(bot, msg_update(1, "/stuck")))
    await asyncio.sleep(0.1)
    assert await drain(dp2["tracker"], timeout=0.2) == 1             # 시간이 지나면 포기한다
    job2.cancel()
    assert await drain(dp2["tracker"], timeout=1) == 0
