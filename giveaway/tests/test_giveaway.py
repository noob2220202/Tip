import asyncio
import dataclasses
import socket

import pytest
from aiogram.types import Update

from bot import store
from bot import texts as T
from bot.eligibility import ERROR, MISSING, OK, Checker
from bot.engine import MAX_CHECK_ERRORS, Engine
from bot.handlers import router
from bot.store import SCHEMA
from bot.texts import TEXTS
from core import users, verifier
from core.bot_runner import assemble
from core.config import Channel
from core.db import open_db
from core.texts import Texts
from testkit.fixtures import ADMIN, TOKEN, cb_update, msg_update

CH = Channel(chat_id="@chan", url="https://t.me/chan")
_n = [50_000]


class FakeChecker:
    def __init__(self):
        self.missing, self.errors, self.calls = set(), set(), []

    async def check(self, uid):
        self.calls.append(uid)
        return ERROR if uid in self.errors else MISSING if uid in self.missing else OK


@pytest.fixture
async def env(make_cfg, bot, tg):
    cfg = make_cfg()
    db = await open_db(cfg.db_path, SCHEMA)
    checker = FakeChecker()
    engine = Engine(bot, db, checker, cfg.admin_ids, per_sec=10_000)
    router._parent_router = None
    dp, _ = await assemble(cfg, bot, db, [router], Texts(TEXTS), {"engine": engine, "checker": checker})

    class E:  # noqa: N801
        pass

    e = E()
    e.tg, e.db, e.dp, e.bot, e.engine, e.checker, e.cfg = tg, db, dp, bot, engine, checker, cfg
    yield e
    await engine.stop_all()
    await db.close()


async def say(e, uid, text):
    await e.dp.feed_update(e.bot, msg_update(uid, text))


async def press(e, uid, data):
    await e.dp.feed_update(e.bot, cb_update(uid, data))


async def photo(e, uid, file_id):
    _n[0] += 1
    await e.dp.feed_update(e.bot, msg_update(uid, None, photo=[{"file_id": file_id, "file_unique_id": f"u{file_id}",
                                                                 "width": 1, "height": 1}]))


def last(e, uid):
    return e.tg.texts_to(uid)[-1]


async def seed(e, ids):
    for i in ids:
        await users.register(e.db, i, f"user{i}")


async def make_draw(e, n=3, codes=("C1", "C2", "C3"), note="", title="추석 이벤트"):
    await press(e, ADMIN, "ga:new")
    await press(e, ADMIN, "gk:draw")
    await say(e, ADMIN, title)
    await say(e, ADMIN, str(n))
    await say(e, ADMIN, "\n".join(codes))
    await press(e, ADMIN, "gi:done")
    await (say(e, ADMIN, note) if note else press(e, ADMIN, "gn:skip"))
    return (await store.list_events(e.db, 1))[0]["id"]


async def make_all(e, content="CODE-ALL", title="전체 이벤트", is_photo=False):
    await press(e, ADMIN, "ga:new")
    await press(e, ADMIN, "gk:all")
    await say(e, ADMIN, title)
    await (photo(e, ADMIN, content) if is_photo else say(e, ADMIN, content))
    await press(e, ADMIN, "gi:done")
    await press(e, ADMIN, "gn:skip")
    return (await store.list_events(e.db, 1))[0]["id"]


async def run(e, eid):
    await press(e, ADMIN, f"gx:run:{eid}")
    if e.engine.tasks:
        await asyncio.gather(*e.engine.tasks.values())


def codes_sent(e, ids):
    """사용자별로 실제로 받은 기프트콘 메시지(🎟 코드 포함) 모음. 실패한 시도는 세지 않는다."""
    out = {uid: [] for uid in ids}
    for _, p in e.tg.sent:
        uid = int(p["chat_id"])
        text = p.get("text") or p.get("caption") or ""
        if uid in out and "🎟" in text:
            out[uid].append(text)
    return out


def received(e, uid):
    """이 사용자에게 실제로 전달된 메시지 수."""
    return sum(1 for _, p in e.tg.sent if int(p["chat_id"]) == uid)


def got_code(text):
    return text.split("<code>")[1].split("</code>")[0]


# ───────────── 일반 사용자 ─────────────

async def test_start_registers_user_and_admin_sees_menu(env):
    await say(env, 7, "/start")
    assert "기프트콘 이벤트 봇" in last(env, 7) and "reply_markup" not in env.tg.of("sendMessage")[-1]
    async with env.db.execute("SELECT is_active FROM users WHERE user_id=7") as cur:
        assert (await cur.fetchone())["is_active"] == 1
    await say(env, ADMIN, "/start")
    assert "ga:new" in env.tg.of("sendMessage")[-1]["reply_markup"]            # 관리자에게만 관리 메뉴 버튼이 붙는다


async def test_non_admin_cannot_use_admin_features(env):
    await say(env, 7, "/start")
    before = len(env.tg.calls)
    await say(env, 7, "/giveaway")
    await say(env, 7, "/giveaways")
    await press(env, 7, "ga:new")
    await press(env, 7, "ga:list")
    await press(env, 7, "gx:run:1")
    assert not any(m == "sendMessage" and "이벤트" in p.get("text", "") and str(p.get("chat_id")) == "7"
                   for m, p in env.tg.calls[before:])
    assert await store.list_events(env.db) == []


# ───────────── 이벤트 만들기 ─────────────

async def test_create_draw_event_flow(env):
    await press(env, ADMIN, "ga:new")
    await press(env, ADMIN, "gk:draw")
    await say(env, ADMIN, "추석 이벤트")
    await say(env, ADMIN, "3")
    await say(env, ADMIN, "AAA\nBBB\n\n")
    assert "2개" in last(env, ADMIN)
    await photo(env, ADMIN, "FILE1")
    assert "3개" in last(env, ADMIN) and "예비" in last(env, ADMIN)
    await press(env, ADMIN, "gi:done")
    await say(env, ADMIN, "12월 31일까지 사용하세요")
    ev = (await store.list_events(env.db))[0]
    assert (ev["kind"], ev["title"], ev["winners"], ev["note"], ev["status"]) == ("draw", "추석 이벤트", 3, "12월 31일까지 사용하세요", "draft")
    assert [(i["kind"], i["content"]) for i in await _items(env, ev["id"])] == [("text", "AAA"), ("text", "BBB"), ("photo", "FILE1")]
    assert "지금 실행" in last(env, ADMIN) or "이벤트를 만들었습니다" in last(env, ADMIN)


async def _items(e, eid):
    async with e.db.execute("SELECT * FROM items WHERE event_id=? ORDER BY id", (eid,)) as cur:
        return list(await cur.fetchall())


async def test_create_all_event_keeps_single_item(env):
    await press(env, ADMIN, "ga:new")
    await press(env, ADMIN, "gk:all")
    await say(env, ADMIN, "전체")
    await say(env, ADMIN, "FIRST CODE\n여러 줄이어도 하나")
    await say(env, ADMIN, "SECOND")                                  # 다시 보내면 바꾼다
    await press(env, ADMIN, "gi:done")
    await press(env, ADMIN, "gn:skip")
    ev = (await store.list_events(env.db))[0]
    assert [i["content"] for i in await _items(env, ev["id"])] == ["SECOND"] and ev["kind"] == "all"


async def test_create_validation(env):
    await press(env, ADMIN, "ga:new"); await press(env, ADMIN, "gk:draw")
    await say(env, ADMIN, "가" * 61)
    assert "다시 보내" in last(env, ADMIN)
    await say(env, ADMIN, "제목")
    for bad in ("0", "501", "abc", "-1"):
        await say(env, ADMIN, bad)
        assert "숫자로 다시" in last(env, ADMIN), bad
    await say(env, ADMIN, "2")
    await press(env, ADMIN, "gi:done")
    assert "먼저 등록" in last(env, ADMIN)
    await say(env, ADMIN, "ONLY-ONE")
    await press(env, ADMIN, "gi:done")
    assert "</b>뿐입니다" in last(env, ADMIN) and "1개 더</b>" in last(env, ADMIN)
    await say(env, ADMIN, "TWO")
    await press(env, ADMIN, "gi:done")
    await say(env, ADMIN, "가" * 301)
    assert "다시 보내" in last(env, ADMIN) and await store.list_events(env.db) == []
    await say(env, ADMIN, "/start")
    assert "만드는 중" in last(env, ADMIN)
    await say(env, ADMIN, "/cancel")
    await say(env, ADMIN, "안녕")
    assert await store.list_events(env.db) == []


async def test_preview_does_not_consume_code(env):
    eid = await make_draw(env, 1, ("SECRET",))
    await press(env, ADMIN, f"gx:view:{eid}")
    assert "미리보기" in last(env, ADMIN) and "SECRET" in last(env, ADMIN)
    assert (await store.item_counts(env.db, eid))["free"] == 1


# ───────────── 추첨 ─────────────

async def test_draw_picks_only_subscribed_distinct_users_and_unique_codes(env):
    await seed(env, range(1, 21))
    await seed(env, [ADMIN])                                         # 관리자는 대상에서 빠진다
    env.checker.missing = set(range(1, 11))                          # 1~10은 구독 안 함
    eid = await make_draw(env, 3, ("C1", "C2", "C3", "C4"), note="사용 기한 12/31")
    await run(env, eid)
    got = {u: v for u, v in codes_sent(env, range(1, 21)).items() if v}
    assert len(got) == 3 and all(u > 10 for u in got) and all(len(v) == 1 for v in got.values())
    codes = [got_code(v[0]) for v in got.values()]
    assert len(set(codes)) == 3 and set(codes) <= {"C1", "C2", "C3", "C4"}        # 같은 코드가 두 사람에게 가지 않는다
    assert "축하합니다" in list(got.values())[0][0] and "사용 기한 12/31" in list(got.values())[0][0]
    assert codes_sent(env, [ADMIN])[ADMIN] == []
    ev = await store.get_event(env.db, eid)
    assert ev["status"] == "done" and (await store.item_counts(env.db, eid))["sent"] == 3
    report = [t for t in env.tg.texts_to(ADMIN) if "끝났습니다" in t][-1]
    assert report.count("user") >= 3 and "추석 이벤트" in report


async def test_draw_is_random_not_first_come(env):
    await seed(env, range(1, 41))
    winners_sets = set()
    for k in range(4):
        env.tg.calls.clear()
        eid = await make_draw(env, 1, (f"R{k}",), title=f"회차{k}")
        await run(env, eid)
        winners_sets.add(tuple(r["user_id"] for r in await store.winners_of(env.db, eid)))
    assert len(winners_sets) > 1                                      # 앞 번호만 계속 뽑히지 않는다


async def test_draw_skips_blocked_users_and_reuses_released_code(env):
    await seed(env, range(1, 9))
    env.tg.forbidden = {1, 2, 3, 4}
    eid = await make_draw(env, 2, ("X1", "X2"))
    await run(env, eid)
    got = {u: v for u, v in codes_sent(env, range(1, 9)).items() if v}
    assert set(got) <= {5, 6, 7, 8} and len(got) == 2
    assert sorted(got_code(v[0]) for v in got.values()) == ["X1", "X2"]       # 차단된 사람에게 간 코드는 되돌려서 다시 썼다
    for blocked in {1, 2, 3, 4} & {r["user_id"] for r in await _tries(env, eid)}:
        async with env.db.execute("SELECT is_active FROM users WHERE user_id=?", (blocked,)) as cur:
            assert (await cur.fetchone())["is_active"] == 0
    assert (await store.item_counts(env.db, eid))["sent"] == 2


async def _tries(e, eid):
    async with e.db.execute("SELECT * FROM tries WHERE event_id=?", (eid,)) as cur:
        return list(await cur.fetchall())


async def test_uncertain_delivery_never_reuses_the_code(env):
    await seed(env, range(1, 7))
    env.tg.server_error = {1, 2, 3}
    eid = await make_draw(env, 2, ("U1", "U2", "U3"))
    await run(env, eid)
    c = await store.item_counts(env.db, eid)
    tries = await store.try_counts(env.db, eid)
    delivered = [got_code(v[0]) for v in codes_sent(env, range(1, 7)).values() if v]
    assert len(delivered) == len(set(delivered))                      # 중복 지급 없음
    assert c["uncertain"] == tries["uncertain"] and c["sent"] == tries["won"]
    assert c["sent"] + c["uncertain"] + c["free"] == 3
    if tries["uncertain"]:
        assert any("확인 필요" in t for t in env.tg.texts_to(ADMIN))
    for uid in (1, 2, 3):                                             # 불확실했던 사람에게는 어떤 코드도 전달되지 않았다
        assert received(env, uid) == 0


async def test_draw_shortage_is_reported(env):
    await seed(env, [1, 2])
    eid = await make_draw(env, 3, ("S1", "S2", "S3"))
    await run(env, eid)
    ev = await store.get_event(env.db, eid)
    assert ev["status"] == "done" and "3명 중 2명" in ev["stop_reason"]
    assert "3명 중 2명" in [t for t in env.tg.texts_to(ADMIN) if "끝났습니다" in t][-1]


async def test_no_double_run_and_no_double_win(env):
    await seed(env, range(1, 6))
    eid = await make_draw(env, 2, ("D1", "D2"))
    await press(env, ADMIN, f"gx:run:{eid}")
    await press(env, ADMIN, f"gx:run:{eid}")                         # 한 번 더 눌러도 이미 실행 중
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")
    await asyncio.gather(*env.engine.tasks.values())
    await press(env, ADMIN, f"gx:run:{eid}")                         # 끝난 뒤에도 다시 실행할 수 없다
    assert (await store.item_counts(env.db, eid))["sent"] == 2
    assert sum(len(v) for v in codes_sent(env, range(1, 6)).values()) == 2


async def test_resume_after_crash_treats_in_flight_as_uncertain(env):
    await seed(env, range(1, 8))
    eid = await make_draw(env, 2, ("K1", "K2", "K3"))
    await store.transition(env.db, eid, ("draft",), "running")
    item = await store.claim_item(env.db, eid, 1)                     # 1번에게 보내던 도중 봇이 죽었다
    await store.set_try(env.db, eid, 1, "sending", item["id"])
    await env.engine.resume_running()
    await asyncio.gather(*env.engine.tasks.values())
    tries = {r["user_id"]: r["result"] for r in await _tries(env, eid)}
    assert tries[1] == "uncertain" and (await store.item_counts(env.db, eid))["uncertain"] == 1
    assert received(env, 1) == 0 and len(env.tg.texts_to(1)) == 0     # 다시 시도하지도 않았다
    delivered = [got_code(v[0]) for v in codes_sent(env, range(2, 8)).values() if v]
    assert len(delivered) == 2 and item["content"] not in delivered     # 남은 코드 2개로 마저 진행, 불확실한 코드는 안 쓴다
    assert (await store.get_event(env.db, eid))["status"] == "done"


async def test_cancel_and_wipe(env):
    await seed(env, [1])
    eid = await make_draw(env, 1, ("W1",))
    await press(env, ADMIN, f"gx:cancel:{eid}")
    assert (await store.get_event(env.db, eid))["status"] == "cancelled"
    await press(env, ADMIN, f"gx:run:{eid}")
    assert received(env, 1) == 0 and env.tg.texts_to(1) == []         # 취소된 이벤트는 실행되지 않는다
    await press(env, ADMIN, f"gx:wipe:{eid}")
    assert [i["content"] for i in await _items(env, eid)] == [""]
    eid2 = await make_draw(env, 1, ("W2",))
    await press(env, ADMIN, f"gx:wipe:{eid2}")                        # 안 끝난 이벤트의 코드는 못 지운다
    assert [i["content"] for i in await _items(env, eid2)] == ["W2"]


# ───────────── 전원 동일 ─────────────

async def test_all_mode_sends_same_code_to_every_subscribed_user(env):
    await seed(env, range(1, 13))
    env.checker.missing = {1, 2, 3}
    env.tg.forbidden = {4}
    eid = await make_all(env, "SAME-CODE")
    await run(env, eid)
    got = {u: v for u, v in codes_sent(env, range(1, 13)).items() if v}
    assert set(got) == set(range(5, 13)) and all(got_code(v[0]) == "SAME-CODE" and len(v) == 1 for v in got.values())
    assert "선착순" in got[5][0] and "구독해 주셔서" in got[5][0]
    t = await store.try_counts(env.db, eid)
    assert (t["won"], t["unsub"], t["blocked"]) == (8, 3, 1)
    assert (await store.get_event(env.db, eid))["status"] == "done"
    await press(env, ADMIN, f"gx:run:{eid}")
    assert sum(len(v) for v in codes_sent(env, range(1, 13)).values()) == 8       # 다시 눌러도 또 보내지 않는다


async def test_all_mode_with_image(env):
    await seed(env, [1, 2])
    eid = await make_all(env, "PHOTO-ID", is_photo=True)
    await run(env, eid)
    sent = env.tg.of("sendPhoto")
    assert sorted(int(p["chat_id"]) for p in sent) == [1, 2] and all(p["photo"] == "PHOTO-ID" for p in sent)
    assert "선착순" in sent[0]["caption"]


async def test_all_mode_uncertain_user_is_not_resent(env):
    await seed(env, [1, 2, 3])
    env.tg.server_error = {2}
    eid = await make_all(env)
    await run(env, eid)
    t = await store.try_counts(env.db, eid)
    assert (t["won"], t["uncertain"]) == (2, 1) and received(env, 2) == 0
    assert any("확인 필요" in x for x in env.tg.texts_to(ADMIN))


# ───────────── 일시 중지와 이어서 실행 ─────────────

async def test_pauses_when_subscription_check_keeps_failing_and_resumes_without_resending(env):
    await seed(env, range(1, 21))
    env.checker.errors = set(range(1, 21))
    eid = await make_all(env, "PAUSE-CODE")
    await run(env, eid)
    ev = await store.get_event(env.db, eid)
    assert ev["status"] == "paused" and "구독 확인이 계속 실패" in ev["stop_reason"]
    assert len(env.checker.calls) == MAX_CHECK_ERRORS and any("일시 중지" in t for t in env.tg.texts_to(ADMIN))
    assert (await store.try_counts(env.db, eid))["won"] == 0

    # 일부는 이미 보낸 상태에서 다시 멈췄다가 이어가도 같은 사람에게 두 번 보내지 않는다
    env.checker.errors = set(range(1, 21)) - set(range(1, 6))           # 1~5만 확인 가능
    await run(env, eid)
    assert (await store.get_event(env.db, eid))["status"] == "paused"
    sent_so_far = {u for u, v in codes_sent(env, range(1, 21)).items() if v}
    assert sent_so_far and sent_so_far <= set(range(1, 6))
    env.checker.errors = set()
    await run(env, eid)
    assert (await store.get_event(env.db, eid))["status"] == "done"
    assert all(len(v) == 1 for v in codes_sent(env, range(1, 21)).values())      # 20명 모두 정확히 한 번씩


async def test_invalid_image_pauses_event_and_releases_code(env):
    await seed(env, [1, 2])
    await press(env, ADMIN, "ga:new"); await press(env, ADMIN, "gk:draw")
    await say(env, ADMIN, "잘못된 이미지"); await say(env, ADMIN, "1")
    await photo(env, ADMIN, "BAD_FILE")
    await press(env, ADMIN, "gi:done"); await press(env, ADMIN, "gn:skip")
    eid = (await store.list_events(env.db, 1))[0]["id"]
    await run(env, eid)
    ev = await store.get_event(env.db, eid)
    assert ev["status"] == "paused" and "기프트콘을 보낼 수 없습니다" in ev["stop_reason"]
    assert (await store.item_counts(env.db, eid))["free"] == 1 and await _tries(env, eid) == []


# ───────────── 목록 · 상세 · 문구 ─────────────

async def test_list_and_detail_views(env):
    await seed(env, range(1, 4))
    eid = await make_draw(env, 1, ("Z1", "Z2"))
    await say(env, ADMIN, "/giveaways")
    assert f"#{eid}" in last(env, ADMIN) and "추첨" in last(env, ADMIN) and "준비 중" in last(env, ADMIN)
    await press(env, ADMIN, f"ge:{eid}")
    assert "대상 후보" in last(env, ADMIN) and "<b>3명</b>" in last(env, ADMIN)
    await run(env, eid)
    await press(env, ADMIN, f"ge:{eid}")
    assert "완료" in last(env, ADMIN) and "받은 분" in last(env, ADMIN)
    await press(env, ADMIN, "ge:999")
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")


def test_delivery_text_escapes_admin_input():
    ev = {"kind": "draw", "title": "<b>제목</b> & 이벤트", "note": "<i>주의</i>"}
    text = T.delivery(ev, "text", "A<B>C")
    assert "&lt;b&gt;제목" in text and "&lt;i&gt;주의" in text and "<code>A&lt;B&gt;C</code>" in text


async def test_store_candidates_exclude_tried_inactive_and_admins(env):
    await seed(env, [1, 2, 3, ADMIN])
    await users.set_active(env.db, 3, False)
    eid = await make_all(env)
    await store.set_try(env.db, eid, 1, "won")
    assert await store.candidates(env.db, eid, env.cfg.admin_ids, False, 10) == [2]
    assert await store.pool_size(env.db, eid, env.cfg.admin_ids) == 1


# ───────────── 구독 확인 · 시작·종료 ─────────────

def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cfg_with(cfg, url=None, channels=(CH,)):
    return dataclasses.replace(cfg, common=dataclasses.replace(cfg.common, channels=tuple(channels), verifier_url=url),
                               env={"VERIFY_SECRET": "s3cret"})


async def test_checker_via_verifier_service_and_direct(make_cfg, bot, tg):
    tg.members[("@chan", 1)] = "member"
    tg.members[("@chan", 2)] = "left"
    port = _port()
    runner = await verifier.start_server(bot, [CH], "s3cret", port)
    try:
        c = Checker(_cfg_with(make_cfg(), f"http://127.0.0.1:{port}"), bot)
        assert [await c.check(1), await c.check(2)] == [OK, MISSING]
    finally:
        await runner.cleanup()
    assert await c.check(1) == ERROR                                    # 검수 서비스가 꺼져 있으면 확인 불가
    direct = Checker(_cfg_with(make_cfg()), bot)                        # 검수 서비스 없이 직접 확인
    assert [await direct.check(1), await direct.check(2)] == [OK, MISSING]
    assert await Checker(_cfg_with(make_cfg(), channels=()), bot).check(2) == OK


async def test_checker_reports_error_when_channel_cannot_be_read(make_cfg, bot, tg):
    tg.bad_chats.add("@chan")
    port = _port()
    runner = await verifier.start_server(bot, [CH], "s3cret", port)
    try:
        assert await Checker(_cfg_with(make_cfg(), f"http://127.0.0.1:{port}"), bot).check(1) == ERROR
    finally:
        await runner.cleanup()
    assert await Checker(_cfg_with(make_cfg()), bot).check(1) == ERROR      # 직접 확인할 때도 같다


async def test_startup_resumes_running_events_and_shutdown_stops(env):
    import dataclasses as dc

    from bot import services

    await seed(env, [1, 2])
    eid = await make_all(env)
    await store.transition(env.db, eid, ("draft",), "running")
    extra = await services.on_startup(env.bot, env.db, dc.replace(env.cfg, env={"SEND_PER_SEC": "5000"}))
    try:
        await asyncio.gather(*extra["engine"].tasks.values())
        assert (await store.get_event(env.db, eid))["status"] == "done" and (await store.try_counts(env.db, eid))["won"] == 2
    finally:
        await services.on_shutdown(extra)
