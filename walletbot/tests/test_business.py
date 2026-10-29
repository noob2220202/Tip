"""/조회 /시세 /환율 (봇 대화와 텔레그램 비즈니스 대화), 비즈니스 연결, 주인 기준 구독 확인."""
import pytest

from bot.handlers import business
from core.config import Channel
from core.subscription import SubscriptionGate
from support import RCPT, U1, U2, W1, W2, last, say, start
from testkit.fixtures import biz_conn, biz_conn_update, biz_update, msg_update

OWNER, PEER = 801, 802
CH = Channel(chat_id="@chan", url="https://t.me/chan", title="채널")


@pytest.fixture(autouse=True)
def _fresh_memory():
    business.RECENT._d.clear()
    business._used.clear()


def gate_of(e) -> SubscriptionGate:
    return next(m for m in e.dp.update.outer_middleware if isinstance(m, SubscriptionGate))


def biz_sent(e):
    return [p for m, p in e.tg.sent if m == "sendMessage" and p.get("business_connection_id")]


async def connect(e, owner=OWNER, conn="bc1", **kw):
    e.tg.business[conn] = biz_conn(owner, conn, **kw)
    await e.dp.feed_update(e.bot, biz_conn_update(owner, conn, **kw))


async def biz(e, sender, text, conn="bc1", reply_text=None, peer=PEER):
    await e.dp.feed_update(e.bot, biz_update(sender, peer, text, conn, reply_text))


def setup_wallet(e, addr=W1, usdt=12_500_000, trx=3_000_000, energy=0):
    e.tron.activate(addr, trx_micro=trx, energy=energy)
    e.tron.usdt_bal[addr] = usdt


# ───────────── 봇 대화 ─────────────

async def test_private_lookup_and_rate(env):
    setup_wallet(env)
    await start(env)
    await say(env, U1, f"/조회 {W1}")
    t = last(env, U1)
    assert "지갑 조회" in t and "12.50" in t and "≈ 17,850원" in t and "3.00" in t and "≈ 1,260원" in t
    assert "대역폭 <b>600</b>" in t and "합계 ≈ <b>19,110원</b>" in t
    await say(env, U1, "/조회")
    assert "를 함께 보내 주십시오" in last(env, U1)
    await say(env, U1, "/환율 100")
    assert "100.00 USDT" in last(env, U1) and "142,800원" in last(env, U1) and "340.00 TRX" in last(env, U1)
    await say(env, U1, "/환율 10만원")
    assert "100,000원" in last(env, U1) and "70.03 USDT" in last(env, U1) and "71.43 USD" in last(env, U1)
    await say(env, U1, "/환율 50trx")
    assert "21,000원" in last(env, U1) and "14.71 USDT" in last(env, U1)
    await say(env, U1, "/환율 $20")
    assert "28,000원" in last(env, U1)
    await say(env, U1, "/환율")
    assert "1 USD" in last(env, U1) and "1,428원" in last(env, U1) and "김프 +2.00%" in last(env, U1)
    await say(env, U1, "/환율 abc")
    assert "이해하지 못했습니다" in last(env, U1)
    await say(env, U1, "/rate 1")
    assert "1,428원" in last(env, U1)


async def test_lookup_inactive_and_contract_and_failure(env):
    env.tron.usdt_bal[W2] = 5_000_000                       # TRC20만 받아서 아직 활성화 안 된 주소도 USDT는 보인다
    await start(env)
    await say(env, U1, f"/조회 {W2}")
    assert "활성화되지 않은" in last(env, U1) and "5.00" in last(env, U1)
    env.tron.activate(RCPT, contract=True)
    await say(env, U1, f"/check {RCPT}")
    assert "컨트랙트" in last(env, U1)
    env.tron.fail = True
    await say(env, U1, f"/조회 {W1}")
    assert "조회에 실패" in last(env, U1)


async def test_lookup_uses_replied_message_address(env):
    setup_wallet(env)
    await start(env)
    upd = msg_update(U1, "/조회", reply_to_message={"message_id": 3, "date": 0, "chat": {"id": U1, "type": "private"},
                                                     "text": f"제 주소는 {W1} 입니다"})
    await env.dp.feed_update(env.bot, upd)
    assert W1 in last(env, U1) and "지갑 조회" in last(env, U1)


async def test_start_from_business_manage_button_shows_guide(env):
    await say(env, U1, "/start bizChat12345")
    assert "비즈니스 연결" in last(env, U1) and "@tip_test_bot" in last(env, U1)
    await say(env, U1, "/business")
    assert "연결 방법" in last(env, U1)


# ───────────── 비즈니스 연결 ─────────────

async def test_connection_is_saved_and_owner_told_once(env):
    await connect(env)
    row = await env.db.execute_fetchall("SELECT owner_id, enabled, can_reply FROM biz_connections WHERE id='bc1'")
    assert tuple(row[0]) == (OWNER, 1, 1) and "비즈니스 연결</b>이 완료" in last(env, OWNER)
    n = len(env.tg.texts_to(OWNER))
    await connect(env)                                       # 같은 상태면 다시 알리지 않는다
    assert len(env.tg.texts_to(OWNER)) == n
    await connect(env, can_reply=False)
    assert "답장 권한" in last(env, OWNER)
    await connect(env, enabled=False)
    assert "해제" in last(env, OWNER)


async def test_owner_commands_reply_in_business_chat(env):
    setup_wallet(env)
    await connect(env)
    await biz(env, OWNER, f"/조회 {W1}")
    sent = biz_sent(env)
    assert len(sent) == 1 and sent[0]["business_connection_id"] == "bc1" and str(sent[0]["chat_id"]) == str(PEER)
    assert "12.50" in sent[0]["text"]
    await biz(env, OWNER, "/시세")
    assert "빗썸 USDT" in biz_sent(env)[-1]["text"] and "reply_markup" not in biz_sent(env)[-1]
    await biz(env, OWNER, "/환율 10만원")
    assert "70.03 USDT" in biz_sent(env)[-1]["text"]


async def test_counterpart_commands_are_ignored(env):
    setup_wallet(env)
    await connect(env)
    await biz(env, PEER, f"/조회 {W1}")
    await biz(env, PEER, "/시세")
    await biz(env, PEER, "/환율 100")
    assert biz_sent(env) == []


async def test_lookup_without_address_uses_last_address_in_chat(env):
    setup_wallet(env)
    await connect(env)
    await biz(env, PEER, f"여기로 보내주세요 {W1}")
    await biz(env, OWNER, "안녕하세요")
    await biz(env, OWNER, "/조회")
    assert W1 in biz_sent(env)[-1]["text"]
    # 다른 대화의 주소는 섞이지 않는다
    await biz(env, OWNER, "/조회", peer=999)
    assert "를 함께 보내 주십시오" in biz_sent(env)[-1]["text"]


async def test_lookup_reply_to_address_message(env):
    setup_wallet(env, W2)
    await connect(env)
    await biz(env, OWNER, "/조회", reply_text=f"주소 {W2}")
    assert W2 in biz_sent(env)[-1]["text"]


async def test_bot_echo_and_disabled_connection_are_ignored(env):
    setup_wallet(env)
    await connect(env)
    await env.dp.feed_update(env.bot, biz_update(OWNER, PEER, f"/조회 {W1}",
                                                 sender_business_bot={"id": 1, "is_bot": True, "first_name": "b"}))
    await env.dp.feed_update(env.bot, biz_update(OWNER, PEER, f"/조회 {W1}", is_from_offline=True))
    assert biz_sent(env) == []
    await connect(env, enabled=False)
    await biz(env, OWNER, f"/조회 {W1}")
    assert biz_sent(env) == []


async def test_unknown_connection_is_fetched_from_telegram(env):
    setup_wallet(env)
    env.tg.business["bc9"] = biz_conn(OWNER, "bc9")
    await biz(env, OWNER, f"/조회 {W1}", conn="bc9")            # 연결 업데이트를 못 받았어도 조회해서 주인을 안다
    assert len(biz_sent(env)) == 1
    await biz(env, OWNER, f"/조회 {W1}", conn="nope")
    assert len(biz_sent(env)) == 1


async def test_owner_rate_limit(env):
    await connect(env)
    for _ in range(25):
        await biz(env, OWNER, "/환율 1")
    assert len(biz_sent(env)) == 20


# ───────────── 구독 확인 (주인 기준) ─────────────

async def test_gate_checks_owner_not_counterpart(make_env):
    e = await make_env(channels=[CH])
    setup_wallet(e)
    await connect(e)
    await biz(e, OWNER, f"/조회 {W1}")
    assert biz_sent(e) == []                                  # 주인이 미가입 → 대화창에는 아무것도 안 보낸다
    assert any("가입했어요" in t for t in e.tg.texts_to(OWNER))  # 주인에게 봇 대화로 안내
    n = len(e.tg.texts_to(OWNER))
    await biz(e, OWNER, f"/조회 {W1}")
    assert len(e.tg.texts_to(OWNER)) == n                     # 안내는 자주 보내지 않는다
    e.tg.members[("@chan", OWNER)] = "member"
    gate_of(e)._biz_blocked.clear()
    await biz(e, OWNER, f"/조회 {W1}")
    assert len(biz_sent(e)) == 1                              # 상대(PEER)는 가입하지 않아도 된다
