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


async def pay(e, amount, txid, token="TRX", sender_hex=None, ts_ms=None):
    from bot import store

    if await store.get_scan(e.db) is None:
        await e.scanner.step()
    tx = FakeTron.tx_trx(PAYH, amount, txid) if token == "TRX" else FakeTron.tx_usdt(PAYH, amount, txid)
    if sender_hex:
        tx["raw_data"]["contract"][0]["parameter"]["value"]["owner_address"] = sender_hex
    e.tron.new_block([tx], ts=ts_ms)
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
    assert s1.calls == [(W1, 65_000, o["ref"])] and s2.calls == []              # 가장 싼 곳 한 번만
    done = await env.desk.get(o["id"])
    assert done["status"] == "done" and done["paid_cost"] == 1_040_000 and done["supplier"] == "s1"
    assert "에너지 도착" in last(env, U1) and "65,000" in last(env, U1) and f"dtx-{o['ref']}" in last(env, U1)
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
    t = last(env, U1)                     # 결제 안내를 지우지 않고 금액·주소·기한을 그대로 보여 준다
    assert "<code>1.04</code>" in t and f"<code>{PAY}</code>" in t and "분 이내" in t
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
    q = await env.desk.quote(a["need"])
    b = await env.desk.create(U1, W1, a["need"], q)                            # 취소하지 않고 바로 새 주문
    assert (await env.desk.get(a["id"]))["status"] == "cancelled"
    assert b["id"] != a["id"] and b["amount"] != a["amount"]
    await pay(env, a["amount"], "pay-old")                                     # 앞 주문 금액도 아직 받을 수 있다
    assert (await env.desk.get(a["id"]))["status"] == "paid" and (await env.desk.get(b["id"]))["status"] == "awaiting"


async def test_order_rate_limit_per_user(env):
    ready(env)
    for _ in range(10):
        o = await buy(env)
        await press(env, U1, f"en:x:{o['id']}")
    assert await env.desk.recent_count(U1, time.time()) == 10
    assert await buy(env) is None                                       # 11번째는 만들지 않는다
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")


# ───────────── 입금 매칭 ─────────────

async def age(e, o, created_ago, expired_ago):
    """주문을 과거에 만든 것처럼 시각을 옮긴다."""
    now = int(time.time())
    await e.db.execute("UPDATE energy_orders SET created_at=?, expires_at=?, status='expired' WHERE id=?",
                       (now - created_ago, now - expired_ago, o["id"]))
    await e.db.commit()


async def test_late_payment_within_window_is_processed(env):
    ready(env)
    o = await buy(env)
    await age(env, o, 1500, 900)                    # 15분 전에 만료된 주문, 입금 블록은 지금(만료 + 15분)
    await pay(env, o["amount"], "late-1")
    assert (await env.desk.get(o["id"]))["status"] == "paid"


async def test_payment_after_late_window_is_not_matched(env):
    ready(env)
    o = await buy(env)
    await age(env, o, 2700, EN.LATE_SECONDS + 120)  # 만료 + 32분 뒤의 입금
    await pay(env, o["amount"], "too-late")
    assert (await env.desk.get(o["id"]))["status"] == "expired"
    assert any("맞는 주문 없음" in t and "too-late" in t for t in admin_texts(env))


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
    assert [c[2] for c in s1.calls] == [o["ref"]] * 3 and s2.calls == []
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
    assert "관리자가 확인한 결과" in last(e, U1) and any("확인 필요" in t for t in admin_texts(e))


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
    assert [c[2] for c in s1.calls] == [o["ref"]] and (await e.desk.get(o["id"]))["status"] == "done"


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


# ───────────── 검토에서 나온 문제의 재현 ─────────────

async def test_payment_during_long_downtime_is_found_from_cursor(env):
    ready(env)
    o = await buy(env)
    t0 = time.time()
    await env.desk.tick(t0)                                                    # 평소처럼 돌던 중(확인 위치 기록)
    env.tron.pay_trx(PAY, PAYH, o["amount"], "while-down")                     # 기한 안에 냈지만 봇이 꺼져 있었다
    await env.desk.tick(t0 + 2500)                                             # 40분 넘게 지나 다시 켜짐
    row = await env.desk.get(o["id"])
    assert row["status"] == "done" and row["txid"] == "while-down"
    assert not any("입금 시간이 지났습니다" in t for t in env.tg.texts_to(U1))


async def test_cursor_moves_only_after_successful_fetch(env):
    ready(env)
    o = await buy(env)
    t0 = time.time()
    await env.desk.tick(t0)
    before = await env.desk._cursor()
    env.tron.fail = True
    await env.desk.tick(t0 + 40)
    assert await env.desk._cursor() == before                                  # 실패하면 위치를 옮기지 않는다
    env.tron.fail = False
    env.tron.pay_trx(PAY, PAYH, o["amount"], "after-fail")
    await env.desk.tick(t0 + 80)
    assert (await env.desk.get(o["id"]))["txid"] == "after-fail"


async def test_duplicate_payment_is_not_credited_to_next_customer(env):
    ready(env)
    ready(env, W2)
    a = await buy(env, U1, W1)
    await pay(env, a["amount"], "a-1")
    await env.desk.tick()
    assert (await env.desk.get(a["id"]))["status"] == "done"
    b = await buy(env, U2, W2)
    assert b["amount"] != a["amount"]                                          # 끝난 주문 금액도 한동안 비켜 간다
    await pay(env, a["amount"], "a-again")                                     # 같은 금액을 실수로 한 번 더
    assert (await env.desk.get(b["id"]))["status"] == "awaiting"
    assert any("맞는 주문 없음" in t and "a-again" in t for t in admin_texts(env))


async def test_payment_prefers_order_created_before_it(env):
    ready(env)
    ready(env, W2)
    a = await buy(env, U1, W1)
    await age(env, a, 2000, 1500)
    # 같은 금액의 새 주문이 생겼다(예약 기간이 끝난 경우를 흉내 내려고 직접 넣는다)
    await env.db.execute("INSERT INTO energy_orders(user_id, receiver, need, energy, amount, cost, status, created_at,"
                         " expires_at, ref) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (U2, W2, 64_285, 65_000, a["amount"], 1, "awaiting", int(time.time()) + 30,
                          int(time.time()) + 630, "ex"))
    await env.db.commit()
    await pay(env, a["amount"], "for-a", ts_ms=int(time.time() * 1000))
    assert (await env.desk.get(a["id"]))["txid"] == "for-a"


async def test_price_blip_is_retried_instead_of_failing(make_env):
    s1 = FakeSupplier("s1", 16)
    e = await make_env(suppliers=[s1])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    s1.mode = "price_fail"
    now = time.time()
    await e.desk.tick(now)
    row = await e.desk.get(o["id"])
    assert row["status"] == "ordering" and s1.calls == [] and "환불" not in last(e, U1)
    s1.mode = "ok"
    await e.desk.tick(now + EN.PRICE_RETRY + 1)
    assert (await e.desk.get(o["id"]))["status"] == "done"


async def test_price_never_comes_back_fails_after_retries(make_env):
    s1 = FakeSupplier("s1", 16)
    e = await make_env(suppliers=[s1])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    s1.mode = "price_fail"
    now = time.time()
    for i in range(EN.PRICE_RETRY_MAX):
        await e.desk.tick(now + i * (EN.PRICE_RETRY + 1))
    assert (await e.desk.get(o["id"]))["status"] == "failed" and s1.calls == []


async def test_unexpected_error_does_not_block_other_orders(make_env):
    s1 = FakeSupplier("s1", 16, mode=["crash", "ok", "ok"])
    e = await make_env(suppliers=[s1, FakeSupplier("s2", 20)])
    ready(e)
    ready(e, W2)
    a = await buy(e, U1, W1)
    b = await buy(e, U2, W2)
    await pay(e, a["amount"], "pa")
    await pay(e, b["amount"], "pb")
    now = time.time()
    await e.desk.tick(now)
    assert (await e.desk.get(a["id"]))["status"] == "ordering"                 # 결과를 모름 → 같은 곳에 다시
    assert (await e.desk.get(b["id"]))["status"] == "done"                     # 다른 주문은 막히지 않는다
    await e.desk.tick(now + EN.UNCERTAIN_RETRY + 1)
    assert (await e.desk.get(a["id"]))["status"] == "done"
    assert [c[2] for c in s1.calls if c[0] == W1] == [a["ref"], a["ref"]] and e.suppliers[1].calls == []


async def test_crashing_price_lookup_skips_only_that_supplier(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, mode="price_crash"), FakeSupplier("s2", 20)])
    ready(e)
    o = await buy(e)
    assert o["amount"] == 1_300_000
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    assert (await e.desk.get(o["id"]))["supplier"] == "s2"


async def test_chain_errors_during_verify_do_not_time_out(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, deliver=False)])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    await e.desk.tick(now)
    e.tron.fail = True
    await e.desk.tick(now + EN.VERIFY_SECONDS + 5)
    assert (await e.desk.get(o["id"]))["status"] == "delivered"               # 체인을 못 읽었으면 실패로 보지 않는다
    e.tron.fail = False
    e.tron.delegate(W1, 65_000)
    await e.desk.tick(now + EN.VERIFY_SECONDS + 10)
    assert (await e.desk.get(o["id"]))["status"] == "done"


async def test_review_order_completes_when_energy_shows_up(make_env):
    s1 = FakeSupplier("s1", 16, mode="uncertain")
    e = await make_env(suppliers=[s1])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    now = time.time()
    for i in range(EN.UNCERTAIN_MAX):
        await e.desk.tick(now + i * 30)
    assert (await e.desk.get(o["id"]))["status"] == "review"
    e.tron.delegate(W1, 65_000)                                                # 사실은 접수됐다
    await e.desk.tick(now + 400)
    assert (await e.desk.get(o["id"]))["status"] == "done" and "에너지 도착" in last(e, U1)
    assert any("자동으로 완료" in t for t in admin_texts(e))


async def test_failed_order_asks_refund_address_and_admin_closes_it(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, mode="reject")], support="@help_desk")
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    assert "@help_desk" in last(e, U1) and f"en:rf:{o['id']}" in buttons(e, U1)
    assert any("환불 필요" in t and f"tg://user?id={U1}" in t for t in admin_texts(e))
    await press(e, U2, f"en:rf:{o['id']}")                                     # 남의 주문
    assert e.tg.of("answerCallbackQuery")[-1].get("show_alert")
    await press(e, U1, f"en:rf:{o['id']}")
    await say(e, U1, "nope")
    assert "올바른 TRON 주소가 아닙니다" in last(e, U1)
    await say(e, U1, W2)
    assert "환불 주소를 받았습니다" in last(e, U1)
    assert (await e.desk.get(o["id"]))["refund_to"] == W2
    assert any("환불 주소 도착" in t and W2 in t for t in admin_texts(e))
    await say(e, ADMIN, "/energy_admin")
    assert W2 in last(e, ADMIN)
    await say(e, ADMIN, f"/energy_done {o['id']} 1.04 TRX 환불했습니다")
    assert "관리자 확인이 끝났습니다" in last(e, U1) and "환불했습니다" in last(e, U1)


async def test_mismatch_offers_refund_button_and_tags_user(env):
    from core.tron import address_to_hex

    ready(env)
    o = await buy(env)
    await pay(env, 1_000_000, "wrong", sender_hex=address_to_hex(W1))
    assert f"en:rf:{o['id']}" in buttons(env, U1)
    assert any("금액 불일치" in t and f"tg://user?id={U1}" in t for t in admin_texts(env))


async def test_admin_can_message_customer(env):
    ready(env)
    o = await buy(env)
    await say(env, ADMIN, f"/energy_msg {o['id']} 환불 주소를 알려 주십시오")
    assert "관리자 메시지" in last(env, U1) and "환불 주소를 알려" in last(env, U1)
    assert "보냈습니다" in last(env, ADMIN)
    await say(env, U2, f"/energy_msg {o['id']} 장난")
    assert not any("장난" in t for t in env.tg.texts_to(U1))


async def test_old_buttons_get_an_answer(env):
    ready(env)
    await buy(env)
    for data in ("en:ok", "en:r:1", "en:w:1"):
        await press(env, U1, data)
        assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")


async def test_menu_commands_work_while_waiting_for_an_address(env):
    await start(env)
    await press(env, U1, "en:buy")
    await say(env, U1, "/지갑")
    assert "내 지갑" in last(env, U1) and "올바른 TRON 주소" not in last(env, U1)


async def test_supplier_refs_are_random_and_unique(env):
    ready(env)
    ready(env, W2)
    a = await buy(env, U1, W1)
    b = await buy(env, U2, W2)
    assert a["ref"] != b["ref"] and a["ref"].startswith("e") and len(a["ref"]) == 17


async def test_migrate_adds_new_columns_to_old_table(make_cfg):
    from bot.energy import migrate
    from core.db import open_db

    db = await open_db(make_cfg().db_path, "CREATE TABLE IF NOT EXISTS energy_orders (id INTEGER PRIMARY KEY, status TEXT);")
    await migrate(db)
    cols = {r[1] for r in await db.execute_fetchall("PRAGMA table_info(energy_orders)")}
    assert {"ref", "refund_to"} <= cols
    await migrate(db)                                                          # 두 번 해도 괜찮다
    await db.close()


async def test_faq_uses_configured_minutes_and_texts_are_formal(make_env):
    e = await make_env(ttl=300)
    await start(e)
    await press(e, U1, "en:faq")
    t = last(e, U1)
    assert "주문 시간(5분)" in t and "30분 안에" in t
    assert "뭔가요" not in t and "되나요" not in t and "무엇입니까" in t
    await press(e, U1, "en:guide")
    assert "지금 0" in last(e, U1)


async def test_review_order_is_not_completed_by_another_orders_energy(make_env):
    s1, s2 = FakeSupplier("s1", 16, mode="uncertain"), FakeSupplier("s2", 20)
    e = await make_env(suppliers=[s1, s2])
    ready(e)
    a = await buy(e)
    await pay(e, a["amount"], "pa")
    now = time.time()
    for i in range(EN.UNCERTAIN_MAX):
        await e.desk.tick(now + i * 30)
    assert (await e.desk.get(a["id"]))["status"] == "review"
    s1.mode = "reject"                                       # 손님이 기다리다 다시 주문 → 다른 공급처가 보낸다
    b = await buy(e)
    await pay(e, b["amount"], "pb")
    await e.desk.tick(now + 400)
    assert (await e.desk.get(b["id"]))["status"] == "done"
    assert (await e.desk.get(a["id"]))["status"] == "review"  # B의 에너지로 A를 완료하지 않는다
    assert any("다른 주문도 있어" in t for t in admin_texts(e))
    assert not any("자동으로 완료되었습니다" in t for t in admin_texts(e))


async def test_chain_only_order_sharing_wallet_goes_to_review(make_env):
    s1 = FakeSupplier("s1", 16, mode=["uncertain"])
    s1.idempotent = False                                    # trxx처럼 다시 보내지 않고 체인만 보는 공급처
    s2 = FakeSupplier("s2", 20)
    e = await make_env(suppliers=[s1, s2])
    ready(e)
    a = await buy(e)
    await pay(e, a["amount"], "pa")
    now = time.time()
    await e.desk.tick(now)
    assert (await e.desk.get(a["id"]))["status"] == "delivered"
    s1.mode = "reject"
    b = await buy(e)
    await pay(e, b["amount"], "pb")
    await e.desk.tick(now + 3)
    assert (await e.desk.get(b["id"]))["status"] == "done"
    assert (await e.desk.get(a["id"]))["status"] == "review"


async def test_order_stuck_for_a_day_is_not_resent(make_env):
    s1 = FakeSupplier("s1", 16)
    e = await make_env(suppliers=[s1])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.db.execute("UPDATE energy_orders SET status='ordering', supplier='s1', next_try=0, paid_at=? WHERE id=?",
                       (int(time.time()) - 25 * 3600, o["id"]))
    await e.db.commit()
    await e.desk.tick()
    assert s1.calls == [] and (await e.desk.get(o["id"]))["status"] == "review"


async def test_refund_for_extra_wrong_payment_on_finished_order(env):
    from core.tron import address_to_hex

    ready(env)
    o = await buy(env)
    await pay(env, 1_000_000, "wrong", sender_hex=address_to_hex(W1))       # 먼저 잘못된 금액
    await pay(env, o["amount"], "right")                                    # 그다음 정확한 금액
    await env.desk.tick()
    assert (await env.desk.get(o["id"]))["status"] == "done"
    await press(env, U1, f"en:rf:{o['id']}")                                # 잘못 보낸 금액은 환불받을 수 있다
    await say(env, U1, W2)
    assert "환불 주소를 받았습니다" in last(env, U1)
    assert any("금액이 다른 입금만" in t and "wrong" in t for t in admin_texts(env))


async def test_no_refund_button_for_normal_finished_order(make_env):
    e = await make_env(support="@help_desk")
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    await say(e, ADMIN, f"/energy_msg {o['id']} 잘 받으셨습니까")
    assert f"en:rf:{o['id']}" not in buttons(e, U1)
    await press(e, U1, f"en:rf:{o['id']}")
    assert "환불할 금액이 없습니다" in last(e, U1) and "@help_desk" in last(e, U1)


async def test_error_before_supplier_call_does_not_block_other_orders(make_env, monkeypatch):
    e = await make_env()
    ready(e)
    ready(e, W2)
    a = await buy(e, U1, W1)
    b = await buy(e, U2, W2)
    await pay(e, a["amount"], "pa")
    await pay(e, b["amount"], "pb")
    real = e.desk._costs
    calls = {"n": 0}

    async def flaky(energy):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("DB 잠김")
        return await real(energy)

    monkeypatch.setattr(e.desk, "_costs", flaky)
    now = time.time()
    await e.desk.tick(now)
    ra = await e.desk.get(a["id"])
    assert ra["status"] == "ordering" and ra["attempts"] == 1 and ra["next_try"] == int(now) + EN.PRICE_RETRY
    assert (await e.desk.get(b["id"]))["status"] == "done"
    await e.desk.tick(now + EN.PRICE_RETRY + 1)
    assert (await e.desk.get(a["id"]))["status"] == "done"


async def test_failed_message_does_not_claim_suppliers_were_silent(make_env):
    e = await make_env(suppliers=[FakeSupplier("s1", 16, mode="reject")])
    ready(e)
    o = await buy(e)
    await pay(e, o["amount"], "p")
    await e.desk.tick()
    assert "응답하지 않습니다" not in last(e, U1) and "받지 못했습니다" in last(e, U1)
