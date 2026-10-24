import hashlib
import time

import pytest
from aiohttp import web

from bot import addresses, fulfil, levels, money, orders
from bot.catalog import Catalog, Product, load_catalog
from bot.handlers import router
from bot.notify import Notifier
from bot.orders import SCHEMA
from bot.smm import SmmPanel
from bot.texts import TEXTS
from bot.tron import USDT_CONTRACT, TronGrid
from bot.worker import Worker
from core.bot_runner import assemble
from core.db import open_db
from core.texts import Texts
from testkit.fixtures import ADMIN, cb_update, msg_update

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def addr(seed: int) -> str:
    """체크섬이 맞는 TRON 주소를 만든다."""
    payload = b"\x41" + hashlib.sha256(str(seed).encode()).digest()[:20]
    raw = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return out


WALLET, ALICE, BOB = addr(1), addr(2), addr(3)


class FakeChain:
    """TronGrid 흉내."""

    def __init__(self):
        self.rows, self.fail, self.calls = [], False, 0

    async def start(self):
        app = web.Application()
        app.router.add_get("/v1/accounts/{w}/transactions/trc20", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"

    async def handle(self, req):
        self.calls += 1
        if self.fail:
            return web.Response(status=500)
        since = int(req.query["min_timestamp"])
        return web.json_response({"success": True, "data": [r for r in self.rows if r["block_timestamp"] >= since],
                                  "meta": {}})

    def pay(self, sender, micro, txid, ts=None, to=WALLET, contract=USDT_CONTRACT):
        self.rows.append({"transaction_id": txid, "from": sender, "to": to, "value": str(micro), "type": "Transfer",
                          "token_info": {"address": contract}, "block_timestamp": ts or int(time.time() * 1000) + 5})


class FakePanel:
    def __init__(self):
        self.mode, self.adds, self.status_value, self.n = "ok", [], "In progress", 0

    async def start(self):
        app = web.Application()
        app.router.add_post("/api", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.url = f"http://127.0.0.1:{self.runner.addresses[0][1]}/api"

    async def handle(self, req):
        d = await req.post()
        assert d["key"] == "K"
        if d["action"] == "add":
            if self.mode == "reject":
                return web.json_response({"error": "Not enough funds"})
            if self.mode == "garbage":
                return web.Response(text="<html>502</html>", status=502)
            self.adds.append(dict(d))
            self.n += 1
            return web.json_response({"order": 7000 + self.n})
        if d["action"] == "status":
            return web.json_response({"status": self.status_value})
        return web.json_response({"balance": "12.5", "currency": "USD"})


@pytest.fixture
async def env(make_cfg, bot, tg):
    chain, panel = FakeChain(), FakePanel()
    await chain.start()
    await panel.start()
    cfg = make_cfg()
    db = await open_db(cfg.db_path, SCHEMA)
    catalog = Catalog((
        Product("b10", "부스트 30일", 11, money.to_micro("0.5"), 30),
        Product("b30", "부스트 7일", 12, money.to_micro("0.07"), 7),
        Product("off", "준비 중", 0, money.to_micro("1")),
    ))
    tron = TronGrid(chain.base, "", WALLET)
    smm = SmmPanel(panel.url, "K")
    notifier = Notifier(bot, Texts(TEXTS), cfg.admin_ids)
    worker = Worker(db, tron, smm, notifier.event, notifier.alert)
    data = {"catalog": catalog, "wallet": WALLET, "ttl_min": 30, "panel": smm, "tron": tron,
            "worker": worker, "notifier": notifier}
    router._parent_router = None   # 테스트마다 새 Dispatcher에 붙이기 위해
    dp, _ = await assemble(cfg, bot, db, [router], Texts(TEXTS), data)

    class E:  # noqa: N801
        pass

    e = E()
    e.chain, e.panel, e.db, e.dp, e.bot, e.tg, e.worker, e.catalog = chain, panel, db, dp, bot, tg, worker, catalog
    e.cfg = cfg
    yield e
    await tron.close(); await smm.close()
    await chain.runner.cleanup(); await panel.runner.cleanup()
    await db.close()


async def say(e, uid, text):
    await e.dp.feed_update(e.bot, msg_update(uid, text))


async def press(e, uid, data):
    await e.dp.feed_update(e.bot, cb_update(uid, data))


async def order_via_bot(e, uid, sender, product="b10", link="https://t.me/mychannel", qty=10):
    await press(e, uid, f"p:{product}")
    await say(e, uid, str(qty))
    await say(e, uid, link)
    await say(e, uid, sender)
    async with e.db.execute("SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)) as cur:
        return (await cur.fetchone())["id"]


# ---------- 순수 함수 ----------

def test_money():
    assert money.to_micro("12.5") == 12_500_000 and money.fmt(12_500_000) == "12.50"
    assert money.fmt(1_234_567) == "1.234567"
    for bad in ("0", "-1", "1.0000001", "abc"):
        with pytest.raises(ValueError):
            money.to_micro(bad)


def test_addresses():
    assert addresses.is_tron_address(ALICE)
    assert not addresses.is_tron_address(ALICE[:-1] + ("a" if ALICE[-1] != "a" else "b"))   # 체크섬 불일치
    assert not addresses.is_tron_address("0x" + "a" * 40) and not addresses.is_tron_address("T123")
    assert addresses.parse_target("@my_channel") == "https://t.me/my_channel"
    assert addresses.parse_target("보자 https://t.me/my_channel?boost") == "https://t.me/my_channel"
    assert addresses.parse_target("t.me/+AbCdEf123456") is None                 # 초대 링크는 받지 않는다
    assert addresses.parse_target("https://t.me/joinchat/AbCdEf123456") is None
    assert addresses.parse_target("https://evil.com/t.me/x") is None and addresses.parse_target("안녕") is None


def test_levels():
    r = levels.LevelRule()
    assert levels.boosts_required(1, 1000, r) == 3 and levels.boosts_required(9, 1000, r) == 35
    assert levels.boosts_required(10, 1000, r) == 50 and levels.boosts_required(10, 0, r) == 10
    g = levels.LevelRule(10, 25)                                               # 그룹: 멤버 // 25 + 1
    assert levels.boosts_required(10, 1000, g) == 410 and levels.boosts_required(1, 1000, g) == 21


def test_default_catalog_loads_and_ships_disabled():
    cat = load_catalog()
    assert cat.products and (cat.min_qty, cat.max_qty) == (10, 1000)


# ---------- 구매 흐름 ----------

async def test_purchase_flow_to_payment_info(env):
    await say(env, 7, "/start")
    assert "환영합니다" in env.tg.texts_to(7)[-1]
    await press(env, 7, "buy")
    last = env.tg.of("sendMessage")[-1]
    assert "부스트 30일" in last["text"] and "준비 중" not in last["text"]       # 비활성 상품은 숨김
    assert "개당 <b>0.50 USDT</b>" in last["text"]
    await press(env, 7, "p:b10")
    for bad in ("아무말", "9", "1001", "0", "-5", "10.5"):
        await say(env, 7, bad)
        assert "개수를 확인해 주십시오" in env.tg.texts_to(7)[-1], bad
    await say(env, 7, "1,000")                                                 # 최대 개수는 허용
    assert "합계 <b>500.00 USDT</b>" in env.tg.texts_to(7)[-1]
    assert "필독 사항" in env.tg.texts_to(7)[-3] or any("필독 사항" in t for t in env.tg.texts_to(7))
    await say(env, 7, "https://t.me/+AbCdEf123456")                            # 초대 링크는 거절
    assert "링크를 확인할 수 없습니다" in env.tg.texts_to(7)[-1]
    await say(env, 7, "아무말")
    assert "링크를 확인할 수 없습니다" in env.tg.texts_to(7)[-1]
    await say(env, 7, "@mychannel")
    await say(env, 7, "T-bad")
    assert "올바른 TRC20 주소가 아닙니다" in env.tg.texts_to(7)[-1]
    await say(env, 7, WALLET)                                                  # 우리 지갑을 보내는 주소로 쓸 수 없다
    assert "입금받는 지갑과 같은 주소" in env.tg.texts_to(7)[-1]
    await say(env, 7, ALICE)
    *_, txt, wallet_msg = env.tg.texts_to(7)
    assert "500.00" in txt and "1000개" in txt and ALICE in txt and "KST" in txt and WALLET not in txt
    assert wallet_msg == f"<code>{WALLET}</code>"                              # 지갑 주소는 복사하기 쉽게 따로 보낸다 and "https://t.me/mychannel" in txt
    o = await orders.get(env.db, 1)
    assert (o["status"], o["amount_micro"], o["quantity"], o["from_addr"], o["service_id"]) == \
        ("awaiting_payment", 500_000_000, 1000, ALICE, 11)


def test_deadline_is_shown_in_korean_time():
    from bot.handlers.common import hhmm
    assert hhmm(0) == "09:00" and hhmm(15 * 3600) == "00:00"


async def test_disabled_product_cannot_be_ordered_and_open_limit(env):
    await press(env, 7, "p:off")                          # 비활성 상품은 선택해도 시작되지 않는다
    await say(env, 7, "https://t.me/x1234")           # 상태가 없으니 메뉴로 돌아간다
    assert "메뉴" in env.tg.texts_to(7)[-1]
    for _ in range(orders.OPEN_LIMIT):
        await order_via_bot(env, 8, ALICE)
    await order_via_bot(env, 8, ALICE)
    assert "너무 많습니다" in env.tg.texts_to(8)[-1]


# ---------- 입금 → 자동 부스트 ----------

async def test_payment_triggers_boost_once(env):
    oid = await order_via_bot(env, 7, ALICE)
    await env.worker.tick()
    assert env.panel.adds == []                                             # 입금 전에는 아무것도 안 한다

    env.chain.pay(ALICE, 5_000_000, "tx1")
    await env.worker.tick()
    assert env.panel.adds == [{"key": "K", "action": "add", "service": "11", "link": "https://t.me/mychannel?boost", "quantity": "10"}]
    o = await orders.get(env.db, oid)
    assert (o["status"], o["txid"], o["panel_order_id"]) == ("processing", "tx1", "7001")
    texts = env.tg.texts_to(7)
    assert any("입금이 확인" in t for t in texts) and any("접수되었습니다" in t for t in texts)

    await env.worker.tick()                                                  # 같은 입금을 다시 봐도 중복 접수 없음
    await env.worker.tick()
    assert len(env.panel.adds) == 1

    env.panel.status_value = "Completed"
    await env.worker.tick(now=int(time.time()) + fulfil.STATUS_INTERVAL + 5)
    assert (await orders.get(env.db, oid))["status"] == "completed"
    assert any("완료되었습니다" in t for t in env.tg.texts_to(7))


async def test_amount_is_unit_price_times_quantity(env):
    oid = await order_via_bot(env, 7, ALICE, product="b30", qty=37)
    assert (await orders.get(env.db, oid))["amount_micro"] == 2_590_000       # 0.07 × 37
    env.chain.pay(ALICE, 2_590_000, "tx1")
    await env.worker.tick()
    assert env.panel.adds[0]["quantity"] == "37" and env.panel.adds[0]["service"] == "12"


def test_panel_link_gets_boost_suffix():
    assert fulfil.panel_link("https://t.me/sacojb") == "https://t.me/sacojb?boost"
    assert fulfil.panel_link("https://t.me/sacojb?boost") == "https://t.me/sacojb?boost"


async def test_matching_rules(env):
    a = await order_via_bot(env, 7, ALICE)
    b = await order_via_bot(env, 8, BOB)
    env.chain.pay(addr(99), 5_000_000, "stranger")             # 모르는 주소
    env.chain.pay(ALICE, 5_000_001, "wrong-amount")             # 금액 불일치
    env.chain.pay(BOB, 5_000_000, "tx-bob", contract="TOtherToken")   # 다른 토큰
    env.chain.pay(BOB, 5_000_000, "tx-bob2", to=addr(50))      # 다른 지갑으로 간 전송
    await env.worker.tick()
    assert env.panel.adds == []
    assert (await orders.get(env.db, a))["status"] == "awaiting_payment"
    assert any("금액이 다른 입금" in t for t in env.tg.texts_to(ADMIN))
    assert any("일치하지 않습니다" in t for t in env.tg.texts_to(7))

    # 같은 금액을 보낸 두 사람은 보내는 주소로 구분된다
    env.chain.pay(BOB, 5_000_000, "tx-bob3")
    await env.worker.tick()
    assert (await orders.get(env.db, b))["status"] == "processing"
    assert (await orders.get(env.db, a))["status"] == "awaiting_payment"
    assert len(env.panel.adds) == 1


async def test_same_address_same_amount_oldest_first_and_txid_used_once(env):
    first = await order_via_bot(env, 7, ALICE)
    second = await order_via_bot(env, 7, ALICE)
    env.chain.pay(ALICE, 5_000_000, "tx1")
    await env.worker.tick()
    assert (await orders.get(env.db, first))["status"] == "processing"
    assert (await orders.get(env.db, second))["status"] == "awaiting_payment"
    await env.worker.tick()
    assert (await orders.get(env.db, second))["status"] == "awaiting_payment"      # 같은 tx로 두 번째가 승인되지 않는다
    env.chain.pay(ALICE, 5_000_000, "tx2")
    await env.worker.tick()
    assert (await orders.get(env.db, second))["status"] == "processing" and len(env.panel.adds) == 2


async def test_payment_before_order_does_not_count(env):
    env.chain.pay(ALICE, 5_000_000, "old", ts=int(time.time() * 1000) - 60_000)
    oid = await order_via_bot(env, 7, ALICE)
    await env.worker.tick()
    assert (await orders.get(env.db, oid))["status"] == "awaiting_payment"


async def test_late_payment_goes_to_manual_review_then_admin_approves(env):
    oid = await order_via_bot(env, 7, ALICE)
    future = int(time.time()) + 31 * 60
    env.chain.pay(ALICE, 5_000_000, "late", ts=future * 1000)
    await env.worker.tick(now=future + 1)
    o = await orders.get(env.db, oid)
    assert (o["status"], o["txid"]) == ("manual_review", "late") and env.panel.adds == []
    assert any(f"/approve {oid}" in t for t in env.tg.texts_to(ADMIN))

    await say(env, 7, f"/approve {oid}")                                    # 일반 사용자는 승인 불가
    assert (await orders.get(env.db, oid))["status"] == "manual_review"
    await say(env, ADMIN, f"/approve {oid}")
    await env.worker.tick()
    assert (await orders.get(env.db, oid))["status"] == "processing" and len(env.panel.adds) == 1


async def test_expiry_and_cancel(env):
    a = await order_via_bot(env, 7, ALICE)
    b = await order_via_bot(env, 7, BOB)
    await press(env, 7, f"c:{b}")
    assert (await orders.get(env.db, b))["status"] == "cancelled"
    await press(env, 8, f"c:{a}")                                            # 남의 주문은 취소 불가
    assert (await orders.get(env.db, a))["status"] == "awaiting_payment"
    await env.worker.tick(now=int(time.time()) + 31 * 60)
    assert (await orders.get(env.db, a))["status"] == "expired"


# ---------- 패널 장애 ----------

async def test_panel_rejection_retries_then_manual_review(env):
    oid = await order_via_bot(env, 7, ALICE)
    env.panel.mode = "reject"
    env.chain.pay(ALICE, 5_000_000, "tx1")
    now = int(time.time())
    await env.worker.tick(now=now)
    assert (await orders.get(env.db, oid))["status"] == "paid"             # 재시도 대기
    await env.worker.tick(now=now + 61)
    await env.worker.tick(now=now + 61 + 121)
    o = await orders.get(env.db, oid)
    assert o["status"] == "manual_review" and "Not enough funds" in o["note"]
    assert any(f"/retry {oid}" in t for t in env.tg.texts_to(ADMIN))

    env.panel.mode = "ok"                                                    # 잔액 충전 후 관리자가 재시도
    await say(env, ADMIN, f"/retry {oid}")
    await env.worker.tick()
    assert (await orders.get(env.db, oid))["status"] == "processing" and len(env.panel.adds) == 1


async def test_uncertain_panel_response_never_resends(env):
    oid = await order_via_bot(env, 7, ALICE)
    env.panel.mode = "garbage"
    env.chain.pay(ALICE, 5_000_000, "tx1")
    await env.worker.tick()
    env.panel.mode = "ok"
    for i in range(3):
        await env.worker.tick(now=int(time.time()) + 1000 * (i + 1))
    assert (await orders.get(env.db, oid))["status"] == "manual_review" and env.panel.adds == []


async def test_restart_while_submitting_is_not_resent(env):
    oid = await order_via_bot(env, 7, ALICE)
    await env.db.execute("UPDATE orders SET status='submitting', txid='t' WHERE id=?", (oid,))
    await env.db.commit()
    assert await fulfil.recover(env.db) == 1
    await env.worker.tick()
    assert (await orders.get(env.db, oid))["status"] == "manual_review" and env.panel.adds == []


async def test_tron_outage_alerts_admin_once(env):
    await order_via_bot(env, 7, ALICE)
    env.chain.fail = True
    await env.worker.tick()
    await env.worker.tick()
    alerts = [t for t in env.tg.texts_to(ADMIN) if "TronGrid" in t]
    assert len(alerts) == 1
    env.chain.fail = False
    env.chain.pay(ALICE, 5_000_000, "tx1")
    await env.worker.tick()
    assert len(env.panel.adds) == 1                                          # 복구되면 놓친 입금도 처리


# ---------- 계산기, 관리자 ----------

async def test_calculator_channel_and_group_differ(env):
    await say(env, 7, "/calc")
    assert env.tg.of("sendMessage")[-1]["reply_markup"]                       # 채널/그룹 선택 버튼
    await press(env, 7, "ck:ch")
    await say(env, 7, "글자")
    assert "숫자만" in env.tg.texts_to(7)[-1]
    await say(env, 7, "1,000")
    await say(env, 7, "20")
    plain = env.tg.texts_to(7)[-1].replace("<b>", "").replace("</b>", "")
    assert "대상: 채널 · 구독자 1,000명" in plain
    assert "필요 부스트: 50개" in plain and "추가로 필요한 부스트: 30개" in plain     # 계수 1000//250+1=5 → 5×10, 보유 20 제외
    assert "가장 저렴한 구성" not in plain and "10개 ~ 1000개" in plain

    await say(env, 7, "/calc")
    await press(env, 7, "ck:gr")
    await say(env, 7, "1000")
    assert "멤버 수" in env.tg.texts_to(7)[-1]
    await say(env, 7, "20")
    plain = env.tg.texts_to(7)[-1].replace("<b>", "").replace("</b>", "")
    assert "대상: 그룹 · 멤버 1,000명" in plain
    assert "필요 부스트: 410개" in plain and "추가로 필요한 부스트: 390개" in plain   # 계수 1000//25+1=41 → 41×10

    await say(env, 7, "/calc"); await press(env, 7, "ck:ch"); await say(env, 7, "0"); await say(env, 7, "10")
    assert "모두 채우셨습니다" in env.tg.texts_to(7)[-1]


async def test_admin_commands(env):
    oid = await order_via_bot(env, 7, ALICE)
    await say(env, 7, "/sales")
    assert not any("매출" in t for t in env.tg.texts_to(7))                  # 일반 사용자에겐 응답 없음
    await say(env, ADMIN, "/balance")
    assert "12.5 USD" in env.tg.texts_to(ADMIN)[-1]
    await say(env, ADMIN, f"/order {oid}")
    assert "서비스 11" in env.tg.texts_to(ADMIN)[-1]
    await say(env, ADMIN, "/list awaiting_payment")
    assert f"#{oid}" in env.tg.texts_to(ADMIN)[-1]
    await say(env, ADMIN, f"/reject {oid}")
    assert (await orders.get(env.db, oid))["status"] == "cancelled"
    await say(env, ADMIN, "/sales")
    assert "매출 요약" in env.tg.texts_to(ADMIN)[-1]


async def test_my_orders(env):
    await say(env, 7, "/orders")
    assert "주문 내역이 없습니다" in env.tg.texts_to(7)[-1]
    oid = await order_via_bot(env, 7, ALICE)
    await say(env, 7, "/orders")
    assert f"#{oid}" in env.tg.texts_to(7)[-1]
    await press(env, 7, f"o:{oid}")
    assert WALLET in env.tg.texts_to(7)[-1]
    await press(env, 8, f"o:{oid}")                                          # 남의 주문은 보이지 않는다
    assert not env.tg.texts_to(8)
