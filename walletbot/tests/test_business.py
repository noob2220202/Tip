"""/조회 /시세 /환율 (봇 대화와 텔레그램 비즈니스 대화), 비즈니스 연결, 주인 기준 구독 확인."""
import pytest

from bot.handlers import business
from core.config import Channel
from core.subscription import SubscriptionGate
from support import RCPT, U1, U2, W1, W2, last, say, start
from testkit.fixtures import biz_conn, biz_conn_update, biz_update, msg_update

OWNER, PEER = 801, 802
CH = Channel(chat_id="@chan", url="https://t.me/chan", title="채널")


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
    # 다른 대화의 주소는 섞이지 않고, 사용법 안내는 대화창이 아니라 주인에게만 간다
    n = len(biz_sent(env))
    await biz(env, OWNER, "/조회", peer=999)
    assert len(biz_sent(env)) == n and "회원님께만 보입니다" in last(env, OWNER)


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
    await biz(e, PEER, f"제 주소 {W1}")                        # 상대(PEER)는 가입하지 않아도 되고, 주소도 기억된다
    await biz(e, OWNER, "/조회")
    assert len(biz_sent(e)) == 1 and W1 in biz_sent(e)[0]["text"]
    assert all("가입" not in t for t in e.tg.texts_to(PEER))


async def test_gate_nudges_owner_only_for_own_commands(make_env):
    e = await make_env(channels=[CH])
    await connect(e)
    assert "구독" in last(e, OWNER)                            # 연결 안내부터 구독이 필요하다고 알린다
    n = len(e.tg.texts_to(OWNER))
    for _ in range(3):
        await biz(e, PEER, "안녕하세요 그냥 대화")               # 상대의 평범한 메시지로는 주인을 귀찮게 하지 않는다
    await biz(e, OWNER, "네 반갑습니다")
    assert len(e.tg.texts_to(OWNER)) == n
    await biz(e, OWNER, "/시세")
    assert len(e.tg.texts_to(OWNER)) == n + 1 and "명령어를 <b>한 번 더</b>" in last(e, OWNER)


async def test_hints_and_errors_go_only_to_owner(env):
    setup_wallet(env)
    await connect(env)
    bad = W1[:-1] + ("a" if W1[-1] != "a" else "b")
    await biz(env, PEER, f"제 주소 {W2}")
    await biz(env, OWNER, f"/조회 {bad}")                     # 잘못 친 주소를 다른 주소로 대신 조회하지 않는다
    assert biz_sent(env) == [] and "올바른 TRON 주소가 아닙니다" in last(env, OWNER)
    await biz(env, OWNER, "/환율 abc")
    assert biz_sent(env) == [] and "이해하지 못했습니다" in last(env, OWNER)
    env.tron.fail = True
    await biz(env, OWNER, f"/조회 {W1}")
    assert biz_sent(env) == [] and "조회에 실패" in last(env, OWNER)
    env.tron.fail = False
    await biz(env, OWNER, "/환율")
    assert "💡" not in biz_sent(env)[-1]["text"]               # 사용법 팁은 대화창에 올리지 않는다


async def test_deleted_or_edited_address_is_forgotten(env):
    from aiogram.types import Update
    from testkit.fixtures import _uid

    setup_wallet(env)
    await connect(env)
    upd = biz_update(PEER, PEER, f"주소 {W1}")
    await env.dp.feed_update(env.bot, upd)
    mid = upd.business_message.message_id
    _uid[0] += 1
    await env.dp.feed_update(env.bot, Update.model_validate({"update_id": _uid[0], "deleted_business_messages": {
        "business_connection_id": "bc1", "chat": {"id": PEER, "type": "private"}, "message_ids": [mid]}}))
    await biz(env, OWNER, "/조회")
    assert biz_sent(env) == []
    upd = biz_update(PEER, PEER, f"주소 {W1}")
    await env.dp.feed_update(env.bot, upd)
    edited = upd.business_message.model_dump(exclude_none=True)
    edited.update(text=f"정정합니다 {W2}", edit_date=1)
    _uid[0] += 1
    await env.dp.feed_update(env.bot, Update.model_validate({"update_id": _uid[0], "edited_business_message": edited}))
    setup_wallet(env, W2)
    await biz(env, OWNER, "/조회")
    assert W2 in biz_sent(env)[-1]["text"]


async def test_reply_failure_is_explained_to_owner_once(env):
    setup_wallet(env)
    await connect(env)
    env.tg.server_error.add(PEER)                             # 대화창 답장이 막힘(24시간 규칙 등)
    await biz(env, OWNER, f"/조회 {W1}")
    await biz(env, OWNER, f"/조회 {W1}")
    notes = [t for t in env.tg.texts_to(OWNER) if "답장하지 못했습니다" in t]
    assert len(notes) == 1 and "24시간" in notes[0]


async def test_private_lookup_is_rate_limited(env):
    setup_wallet(env)
    await start(env)
    for _ in range(21):
        await say(env, U1, f"/조회 {W1}")
    assert "너무 잦습니다" in last(env, U1)
    await say(env, U1, f"/조회 {W1[:-1]}x")
    assert "올바른 TRON 주소가 아닙니다" in last(env, U1)


async def test_compound_korean_amounts(env):
    await start(env)
    await say(env, U1, "/환율 1억5천만")
    assert "150,000,000원" in last(env, U1)
    await say(env, U1, "/환율 1천만원")
    assert "10,000,000원" in last(env, U1)


async def test_guide_discloses_memory_and_24h_rule(env):
    await say(env, U1, "/business")
    t = last(env, U1)
    assert "마지막으로 나온 지갑 주소 하나" in t and "24시간" in t


async def test_node_error_is_not_shown_as_zero_usdt(env):
    setup_wallet(env)
    env.tron.const_error = True                               # 노드 오류가 HTTP 200 + result.code로 온다
    await start(env)
    await say(env, U1, f"/조회 {W1}")
    assert "조회에 실패" in last(env, U1) and "0.00" not in last(env, U1)
