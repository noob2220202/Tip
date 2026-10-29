"""에너지 임대: 주문 단계, 가격, 입금 매칭, 공급처 자동 전환·중복 방지, 체인 확인, 관리자 알림."""
import json
import time

from bot import energy as EN
from bot.energy import SIM_EMPTY, Desk, order_energy
from support import (PAY, PAYH, RCPT, U1, U2, W1, W2, FakeSupplier, FakeTron, last, press, say, start)
from testkit.fixtures import ADMIN


def buttons(e, uid):
    for m, p in reversed(e.tg.calls):
        if m in ("sendMessage", "editMessageText") and str(p.get("chat_id")) == str(uid) and p.get("reply_markup"):
            return [b.get("callback_data") for row in json.loads(p["reply_markup"])["inline_keyboard"] for b in row]
    return []


def ready(e, addr=W1, usdt=50_000_000, energy=0):
    e.tron.activate(addr, energy=energy)
    e.tron.usdt_bal[addr] = usdt


async def buy(e, uid=U1, receiver=W1, has_usdt=True, recipient=None):
    await start(e, uid)
    await press(e, uid, "en:buy")
    await say(e, uid, receiver)
    if recipient:
        await say(e, uid, recipient)
    else:
        await press(e, uid, f"en:r:{int(has_usdt)}")
    await press(e, uid, "en:ok")
    return await e.desk.open_order(uid)


async def pay(e, amount, txid, token="TRX", sender_hex=None):
    from bot import store

    if await store.get_scan(e.db) is None:
        await e.scanner.step()
    tx = FakeTron.tx_trx(PAYH, amount, txid) if token == "TRX" else FakeTron.tx_usdt(PAYH, amount, txid)
    if sender_hex:
        tx["raw_data"]["contract"][0]["parameter"]["value"]["owner_address"] = sender_hex
    e.tron.new_block([tx])
    while (await store.get_scan(e.db))["last_block"] < e.tron.head:
        await e.scanner.step()


def admin_texts(e):
    return e.tg.texts_to(ADMIN)


# ───────────── 순수 계산 ─────────────

def test_order_energy_rounds_up_with_buffer():
    assert order_energy(64_285, 65_000) == 65_000
    assert order_energy(130_285, 65_000) == 131_000
    assert order_energy(10_000, 65_000) == 65_000
    assert order_energy(65_000, 61_000) == 66_000


# ───────────── 소개 화면 ─────────────

async def test_intro_shows_live_prices_and_promo(env):
    await start(env)
    await press(env, U1, "en")
    t = last(env, U1)
    assert "1.04 TRX" in t and "2.096 TRX" in t           # 65k×16, 131k×16 sun
    assert "6.43 TRX" in t and "13.03 TRX" in t and "84% 절약" in t
    assert "원가 그대로" in t and "≈ 437원" in t             # 1.04 × 420원
    assert "en:buy" in buttons(env, U1)


async def test_intro_when_disabled_has_no_buy_button(make_env):
    e = await make_env(pay="")
    await start(e)
    await press(e, U1, "en")
    assert "준비 중" in last(e, U1) and "en:buy" not in buttons(e, U1)
    await press(e, U1, "en:buy")
    assert e.tg.of("answerCallbackQuery")[-1].get("show_alert")


async def test_guide_and_faq(env):
    await start(env)
    await press(env, U1, "en:guide")
    assert "처음 쓰시는 분" in last(env, U1) and "개인키" in last(env, U1)
    await press(env, U1, "en:faq")
    assert "거래소에서 보내도" in last(env, U1)


# ───────────── 주문 단계 ─────────────

async def test_full_flow_cheapest_supplier_and_delivery(env):
    ready(env)
    o = await buy(env)
    assert o and o["energy"] == 65_000 and o["amount"] == 1_040_000 and o["need"] == 64_285
    texts = env.tg.texts_to(U1)
    assert any("정확히 이 금액" in t and "<code>1.04</code>" in t and "10분 이내" in t for t in texts)
    assert texts[-1] == f"<code>{PAY}</code>"                                   # 주소는 따로 보내 복사하기 쉽게
    await pay(env, 1_040_000, "pay-1")
    assert "입금을 확인했습니다" in last(env, U1)
    await env.desk.tick()
    s1, s2 = env.suppliers
    assert s1.calls == [(W1, 65_000, f"e{o['id']}")] and s2.calls == []         # 가장 싼 곳 한 번만
    done = await env.desk.get(o["id"])
    assert done["status"] == "done" and done["paid_cost"] == 1_040_000 and done["supplier"] == "s1"
    assert "에너지 도착" in last(env, U1) and "65,000" in last(env, U1) and "dtx-e" in last(env, U1)
    await env.desk.tick()
    assert len(s1.calls) == 1                                                   # 다시 주문하지 않는다


async def test_receiver_must_be_active_personal_wallet(env):
    await start(env)
    await press(env, U1, "en:buy")
    await say(env, U1, "hello")
    assert "올바른 TRON 주소가 아닙니다" in last(env, U1)
    await say(env, U1, W1)                                                      # 활성화 안 됨
    assert "활성화되지 않았습니다" in last(env, U1)
    env.tron.activate(W2, contract=True)
    await say(env, U1, W2)
    assert "컨트랙트" in last(env, U1)
    await say(env, U1, PAY)
    assert "결제 받는 주소" in last(env, U1)


async def test_registered_wallets_can_be_picked(env):
    from support import register

    await register(env)
    ready(env)
    await press(env, U1, "en:buy")
    w = (await __import__("bot.store", fromlist=["x"]).wallets_of(env.db, U1))[0]
    assert f"en:w:{w['id']}" in buttons(env, U1)
    await press(env, U1, f"en:w:{w['id']}")
    assert "USDT를 받을 주소" in last(env, U1)


async def test_recipient_address_is_simulated_exactly(env):
    ready(env)
    env.tron.activate(RCPT)
    env.tron.sim[RCPT] = 98_765
    o = await buy(env, recipient=RCPT)
    assert o["need"] == 98_765 and o["energy"] == 100_000


async def test_unknown_recipient_uses_empty_address_double_energy(env):
    ready(env)
    o = await buy(env, has_usdt=False)
    assert o["need"] == 130_285 and o["energy"] == 131_000 and o["amount"] == 2_096_000


async def test_simulation_falls_back_when_user_has_no_usdt(env):
    ready(env, usdt=0)                         # 내 지갑에 USDT가 없어 모의 실행이 되돌려진다 → 다른 주소로 다시 계산
    o = await buy(env)
    assert o["need"] == 64_285


async def test_simulation_unavailable_uses_recipient_balance(env):
    ready(env)
    env.tron.sim_fail = True
    env.tron.usdt_bal[RCPT] = 0
    o = await buy(env, recipient=RCPT)
    assert o["need"] == EN.FALLBACK_NEED[False]


async def test_enough_energy_already_no_order(env):
    ready(env, energy=70_000)
    await start(env)
    await press(env, U1, "en:buy")
    await say(env, U1, W1)
    await press(env, U1, "en:r:1")
    assert "이미 에너지가" in last(env, U1) and await env.desk.open_order(U1) is None


async def test_same_address_as_recipient_rejected(env):
    ready(env)
    await start(env)
    await press(env, U1, "en:buy")
    await say(env, U1, W1)
    await say(env, U1, W1)
    assert "같은 주소" in last(env, U1)


async def test_stale_quote_is_recalculated(env, monkeypatch):
    ready(env)
    await start(env)
    await press(env, U1, "en:buy")
    await say(env, U1, W1)
    await press(env, U1, "en:r:1")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 500)
    await press(env, U1, "en:ok")
    assert any("다시 계산했습니다" in t for t in env.tg.texts_to(U1))
    assert await env.desk.open_order(U1) is None


async def test_open_order_is_shown_again_and_can_be_cancelled(env):
    ready(env)
    o = await buy(env)
    await press(env, U1, "en:buy")
    assert f"주문 #{o['id']}" in env.tg.texts_to(U1)[-2]
    await press(env, U2, f"en:x:{o['id']}")                                     # 남의 주문은 못 건드린다
    assert (await env.desk.get(o["id"]))["status"] == "awaiting"
    await press(env, U1, f"en:x:{o['id']}")
    assert (await env.desk.get(o["id"]))["status"] == "cancelled" and "보내지 마십시오" in last(env, U1)


async def test_status_and_order_list(env):
    ready(env)
    o = await buy(env)
    await press(env, U1, f"en:o:{o['id']}")
    assert "입금 대기" in last(env, U1)
    await press(env, U2, f"en:o:{o['id']}")
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")
    await press(env, U1, "en:list")
    assert f"#{o['id']}" in last(env, U1)


# ───────────── 금액 끝자리 ─────────────

async def test_concurrent_orders_get_unique_amounts(env):
    ready(env)
    ready(env, W2)
    a = await buy(env, U1, W1)
    b = await buy(env, U2, W2)
    assert a["amount"] == 1_040_000 and b["amount"] == 1_041_000
    await pay(env, 1_041_000, "pay-b")
    assert (await env.desk.get(b["id"]))["status"] == "paid"
    assert (await env.desk.get(a["id"]))["status"] == "awaiting"


async def test_amounts_of_recent_expired_orders_stay_reserved(env):
    ready(env)
    ready(env, W2)
    a = await buy(env, U1, W1)
    await env.desk.tick(time.time() + 700)                                     # 만료
    assert (await env.desk.get(a["id"]))["status"] == "expired" and "입금 시간이 지났습니다" in last(env, U1)
    b = await buy(env, U2, W2)
    assert b["amount"] != a["amount"]


async def test_new_order_cancels_previous_open_one(env):
    ready(env)
    a = await buy(env)
    await press(env, U1, f"en:x:{a['id']}")
    b = await buy(env)
    assert b["id"] != a["id"] and b["amount"] != a["amount"]


async def test_order_rate_limit_per_user(env):
    ready(env)
    for _ in range(10):
        o = await buy(env)
        await press(env, U1, f"en:x:{o['id']}")
    assert await env.desk.recent_count(U1, time.time()) == 10
    assert await buy(env) is None                                       # 11번째는 만들지 않는다
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")


# ───────────── 입금 매칭 ─────────────

async def test_late_payment_within_window_is_processed(env):
    ready(env)
    o = await buy(env)
    await env.desk.tick(time.time() + 700)
    await pay(env, o["amount"], "late-1")
    assert (await env.desk.get(o["id"]))["status"] == "paid"


async def test_payment_to_cancelled_order_still_processed(env):
    ready(env)
    o = await buy(env)
    await press(env, U1, f"en:x:{o['id']}")
    await pay(env, o["amount"], "after-cancel")
    assert (await env.desk.get(o["id"]))["status"] == "paid"


async def test_wrong_amount_from_order_wallet_notifies_user_and_admin(env):
    from support import mk

    ready(env)
    o = await buy(env)
    await pay(env, 1_000_000, "wrong-1", sender_hex=__import__("core.tron", fromlist=["x"]).address_to_hex(W1))
    assert (await env.desk.get(o["id"]))["status"] == "awaiting"
    assert "금액이 다릅니다" in last(env, U1)
    assert any("금액 불일치" in t for t in admin_texts(env))


async def test_unknown_usdt_and_dust_payments(env):
    ready(env)
    await buy(env)
    await pay(env, 3_000_000, "stranger")
    await pay(env, 5_000_000, "usdt-in", token="USDT")
    await pay(env, 100_000, "dust")
    adm = admin_texts(env)
    assert any("맞는 주문 없음" in t and "stranger" in t for t in adm)
    assert any("USDT로 입금됨" in t for t in adm)
    assert not any("dust" in t for t in adm)


async def test_same_payment_processed_once(env):
    ready(env)
    o = await buy(env)
    await pay(env, o["amount"], "dup")
    await env.desk.on_deposits([__import__("bot.chain", fromlist=["x"]).Deposit("dup", "TRX", "T", o["amount"], 0, PAY)])
    rows = await env.db.execute_fetchall("SELECT COUNT(*) FROM energy_payments WHERE txid='dup'")
    assert rows[0][0] == 1 and len([t for t in env.tg.texts_to(U1) if "입금을 확인했습니다" in t]) == 1


async def test_catch_up_finds_payments_missed_by_scan(env):
    ready(env)
    o = await buy(env)
    env.tron.pay_trx(PAY, PAYH, o["amount"], "missed")                            # 블록 스캔은 못 봤고 내역에만 있다
    await env.desk.tick()
    assert (await env.desk.get(o["id"]))["status"] == "done"


async def test_payment_wallet_registered_as_watch_wallet_still_alerts_owner(env):
    from support import register

    await register(env, uid=U2, addr=PAY, label="결제")
    ready(env)
    o = await buy(env)
    await pay(env, o["amount"], "both")
    assert (await env.desk.get(o["id"]))["status"] == "paid"
    assert any("입금 알림" in t for t in env.tg.texts_to(U2))


# ───────────── 공급처 실패 처리 ─────────────

async def test_rejected_supplier_fails_over_to_next(make_env):
    s1, s2 = FakeSupplier("s1", 16, mode="reject"), FakeSupplier("s2", 20)
    e = await make_env(suppliers=[s1, s2])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    assert len(s1.calls) == 1 and len(s2.calls) == 1
    row = await e.desk.get(o["id"])
    assert row["status"] == "done" and row["supplier"] == "s2" and row["tried"] == "s1"


async def test_low_balance_alerts_admin(make_env):
    s1, s2 = FakeSupplier("s1", 16, mode="low"), FakeSupplier("s2", 20)
    e = await make_env(suppliers=[s1, s2])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    assert any("s1" in t and "잔고가 부족" in t for t in admin_texts(e))
    assert (await e.desk.get(o["id"]))["status"] == "done"


async def test_uncertain_never_fails_over_and_retries_same_ref(make_env):
    s1, s2 = FakeSupplier("s1", 16, mode=["uncertain", "uncertain", "ok"]), FakeSupplier("s2", 20)
    e = await make_env(suppliers=[s1, s2])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    await e.desk.tick(now)
    assert (await e.desk.get(o["id"]))["status"] == "ordering" and s2.calls == []
    await e.desk.tick(now + 5)                                                   # 아직 다시 물을 때가 아니다
    assert len(s1.calls) == 1
    await e.desk.tick(now + 25)
    await e.desk.tick(now + 50)
    assert [c[2] for c in s1.calls] == [f"e{o['id']}"] * 3 and s2.calls == []
    assert (await e.desk.get(o["id"]))["status"] == "done"


async def test_too_many_uncertain_goes_to_review(make_env):
    s1 = FakeSupplier("s1", 16, mode="uncertain")
    e = await make_env(suppliers=[s1, FakeSupplier("s2", 20)])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    for i in range(EN.UNCERTAIN_MAX):
        await e.desk.tick(now + i * 30)
    row = await e.desk.get(o["id"])
    assert row["status"] == "review" and len(s1.calls) == EN.UNCERTAIN_MAX
    assert "관리자가 직접 확인" in last(e, U1) and any("확인 필요" in t for t in admin_texts(e))


async def test_rejection_after_uncertain_is_not_failed_over(make_env):
    s1, s2 = FakeSupplier("s1", 16, mode=["uncertain", "reject", "ok"]), FakeSupplier("s2", 20)
    e = await make_env(suppliers=[s1, s2])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    for i in range(3):
        await e.desk.tick(now + i * 30)
    assert s2.calls == [] and (await e.desk.get(o["id"]))["status"] == "done"


async def test_all_rejected_fails_with_refund_notice(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, mode="reject"), FakeSupplier("s2", 20, mode="reject")])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    assert (await e.desk.get(o["id"]))["status"] == "failed"
    assert "환불" in last(e, U1) and "USDT는 아직 보내지" in last(e, U1)
    assert any("환불 필요" in t for t in admin_texts(e))


async def test_too_expensive_fallback_is_not_used(make_env):
    s1, s2 = FakeSupplier("s1", 16), FakeSupplier("s2", 40)
    e = await make_env(suppliers=[s1, s2], max_loss=500_000)
    ready(e)
    o = await buy(e)
    s1.mode = "reject"
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    assert s2.calls == [] and (await e.desk.get(o["id"]))["status"] == "failed"


async def test_restart_while_ordering_asks_same_supplier_again(make_env):
    s1 = FakeSupplier("s1", 16)
    e = await make_env(suppliers=[s1, FakeSupplier("s2", 20)])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    # 공급처를 부르던 중에 꺼진 상황
    await e.db.execute("UPDATE energy_orders SET status='ordering', supplier='s1', next_try=9999999999 WHERE id=?", (o["id"],))
    await e.db.commit()
    await e.desk.recover()
    await e.desk.tick()
    assert [c[2] for c in s1.calls] == [f"e{o['id']}"] and (await e.desk.get(o["id"]))["status"] == "done"


async def test_delivery_not_seen_on_chain_goes_to_review(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, deliver=False)])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    await e.desk.tick(now)
    assert (await e.desk.get(o["id"]))["status"] == "delivered"
    await e.desk.tick(now + EN.VERIFY_SECONDS + 5)
    assert (await e.desk.get(o["id"]))["status"] == "review" and "늦어지고" in last(e, U1)


async def test_margin_is_added_to_price(make_env):
    e = await make_env(margin=300_000)
    ready(e)
    o = await buy(e)
    assert o["amount"] == 1_340_000


async def test_supplier_price_failure_is_skipped(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, mode="price_fail"), FakeSupplier("s2", 20)])
    ready(e)
    o = await buy(e)
    assert o["amount"] == 1_300_000


async def test_no_prices_at_all(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, mode="price_fail")])
    ready(e)
    await start(e)
    await press(e, U1, "en:buy")
    await say(e, U1, W1)
    await press(e, U1, "en:r:1")
    assert "가격을 가져오지 못했습니다" in last(e, U1)


# ───────────── 관리자 ─────────────

async def test_admin_summary_and_resolve(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, mode="reject")])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    await say(e, ADMIN, "/energy_admin")
    t = last(e, ADMIN)
    assert "에너지 임대 현황" in t and f"#{o['id']}" in t and "s1" in t
    await say(e, U1, "/energy_admin")
    assert "현황" not in last(e, U1)
    await say(e, ADMIN, f"/energy_done {o['id']} 환불 완료")
    assert (await e.desk.get(o["id"]))["status"] == "resolved"


async def test_supplier_balance_check_alerts_when_low(make_env):
    s1 = FakeSupplier("s1", 16)
    s1.bal = 10 * 1_000_000
    e = await make_env(suppliers=[s1])
    await e.desk.tick()
    assert any("잔고가 부족" in t for t in admin_texts(e)) and e.desk.balances["s1"] == 10_000_000


async def test_non_idempotent_supplier_is_never_asked_twice(make_env):
    s1 = FakeSupplier("s1", 16, mode=["uncertain", "ok"])
    s1.idempotent = False
    s2 = FakeSupplier("s2", 20)
    e = await make_env(suppliers=[s1, s2])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    await e.desk.tick(now)
    assert (await e.desk.get(o["id"]))["status"] == "delivered" and len(s1.calls) == 1
    e.tron.delegate(W1, 65_000)                    # 실제로는 접수돼서 에너지가 들어왔다
    await e.desk.tick(now + 3)
    assert (await e.desk.get(o["id"]))["status"] == "done" and len(s1.calls) == 1 and s2.calls == []


async def test_non_idempotent_uncertain_without_energy_goes_to_review(make_env):
    s1 = FakeSupplier("s1", 16, mode="uncertain")
    s1.idempotent = False
    e = await make_env(suppliers=[s1, FakeSupplier("s2", 20)])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    await e.desk.tick(now)
    await e.desk.tick(now + EN.VERIFY_SECONDS + 5)
    assert (await e.desk.get(o["id"]))["status"] == "review" and len(s1.calls) == 1


async def test_restart_with_non_idempotent_supplier_checks_chain_only(make_env):
    s1 = FakeSupplier("s1", 16)
    s1.idempotent = False
    e = await make_env(suppliers=[s1])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.db.execute("UPDATE energy_orders SET status='ordering', supplier='s1', next_try=0 WHERE id=?", (o["id"],))
    await e.db.commit()
    await e.desk.tick()
    assert s1.calls == [] and (await e.desk.get(o["id"]))["status"] == "delivered"
