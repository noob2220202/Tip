"""채널 가입 검수 서비스: 검수 봇(서버)과 다른 봇의 게이트(클라이언트)."""
import dataclasses

import aiohttp
import pytest
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core import verifier
from core.bot_runner import assemble
from core.config import Channel
from core.subscription import SubscriptionGate, self_check
from core.texts import Texts
from core.verifier import SECRET_HEADER, VerifierClient, VerifierError
from testkit.fixtures import cb_update, msg_update

CH = Channel(chat_id="@chan", url="https://t.me/chan", title="채널")
SECRET = "s3cret-value"


@pytest.fixture
async def service(bot, tg):
    """검수 봇 프로세스를 흉내낸다: 127.0.0.1의 임의 포트에서 검수 서비스를 연다."""
    port = _free_port()
    runner = await verifier.start_server(bot, [CH], SECRET, port)
    yield f"http://127.0.0.1:{port}"
    await runner.cleanup()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def with_verifier(cfg, url, secret=SECRET, fail_mode="block"):
    return dataclasses.replace(
        cfg,
        env={**cfg.env, "VERIFY_SECRET": secret},
        common=dataclasses.replace(cfg.common, verifier_url=url, fail_mode=fail_mode),
    )


async def test_service_reports_missing_and_joined(service, tg):
    client = VerifierClient(service, SECRET)
    assert await client.check(10) == {"missing": ["@chan"], "errors": []}
    tg.members[("@chan", 10)] = "member"
    assert await client.check(10) == {"missing": [], "errors": []}


async def test_service_rejects_wrong_or_missing_secret(service):
    with pytest.raises(VerifierError, match="401"):
        await VerifierClient(service, "wrong").check(1)
    async with aiohttp.ClientSession() as http:
        async with http.get(service + "/check", params={"user_id": 1}) as r:
            assert r.status == 401
        async with http.get(service + "/check", headers={SECRET_HEADER: SECRET}) as r:
            assert r.status == 400


async def test_service_refuses_to_start_without_secret(bot):
    with pytest.raises(RuntimeError):
        verifier.build_app(bot, [CH], "")


async def test_service_reports_api_errors_separately(service, tg, monkeypatch):
    from aiogram.exceptions import TelegramBadRequest

    async def broken(*a, **k):
        raise TelegramBadRequest(method=None, message="chat not found")

    monkeypatch.setattr("aiogram.Bot.get_chat_member", broken)
    assert await VerifierClient(service, SECRET).check(5) == {"missing": [], "errors": ["@chan"]}


async def test_gate_uses_verifier_end_to_end(make_cfg, bot, tg, db, service):
    cfg = with_verifier(make_cfg(channels=[CH]), service)
    router = Router(name="ping_v")

    @router.message(Command("ping"))
    async def ping(message: Message) -> None:
        await message.answer("퐁")

    dp, _ = await assemble(cfg, bot, db, [router], Texts())
    await dp.feed_update(bot, msg_update(10, "/ping"))
    assert tg.of("sendMessage")[-1]["text"] != "퐁"                # 가입 안내
    tg.members[("@chan", 10)] = "member"
    await dp.feed_update(bot, cb_update(10, "gate:check"))
    assert "퐁" in tg.texts_to(10)                                 # 가입 후 확인 → 이어서 처리


@pytest.mark.parametrize("mode,blocked", [("block", True), ("allow", False)])
async def test_gate_when_verifier_is_down_follows_fail_mode(make_cfg, bot, db, mode, blocked):
    cfg = with_verifier(make_cfg(channels=[CH]), f"http://127.0.0.1:{_free_port()}", fail_mode=mode)
    gate = SubscriptionGate(cfg, db, Texts())
    assert bool(await gate.missing(bot, 1)) is blocked


@pytest.mark.parametrize("mode,blocked", [("block", True), ("allow", False)])
async def test_gate_when_verifier_secret_is_wrong_follows_fail_mode(make_cfg, bot, db, service, mode, blocked):
    cfg = with_verifier(make_cfg(channels=[CH]), service, secret="wrong", fail_mode=mode)
    assert bool(await SubscriptionGate(cfg, db, Texts()).missing(bot, 1)) is blocked


async def test_self_check_through_service(make_cfg, bot, tg, service):
    cfg = with_verifier(make_cfg(channels=[CH]), service)
    problems = await self_check(bot, cfg)
    assert len(problems) == 1                                     # 검수 봇이 채널 관리자가 아님
    tg.members[("@chan", 1)] = "administrator"
    assert await self_check(bot, cfg) == []


async def test_self_check_reports_unreachable_service(make_cfg, bot, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("core.subscription.asyncio.sleep", no_sleep)
    cfg = with_verifier(make_cfg(channels=[CH]), f"http://127.0.0.1:{_free_port()}")
    problems = await self_check(bot, cfg)
    assert len(problems) == 1 and "검수 서비스" in problems[0] and "VERIFY_SECRET" in problems[0]
    assert await self_check(bot, cfg, direct=True) != []          # direct=True는 서비스를 거치지 않고 직접 점검
