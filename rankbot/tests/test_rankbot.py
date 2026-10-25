import asyncio
import json
import time

import pytest
from aiogram.types import Update

from bot import schedule, store
from bot.crypto import TokenBox
from bot.fleet import Fleet
from bot.handlers import router
from bot.settler import Settler
from bot.store import SCHEMA
from bot.texts import TEXTS
from core.bot_runner import assemble
from core.db import open_db
from core.texts import Texts
from testkit.fixtures import ADMIN, cb_update, msg_update

KEY = "Qm9vc3RUZXN0S2V5Qm9vc3RUZXN0S2V5Qm9vc3RUZXM="   # 테스트용 Fernet 키 (32바이트 base64)
OWNER, OTHER = 501, 502
ALPHA, BETA = 111111, 222222
TOKEN_A, TOKEN_B = f"{ALPHA}:" + "A" * 35, f"{BETA}:" + "B" * 35
GROUP, GROUP2 = -100123, -100456
_n = [10_000]


def gupd(chat, user, text=None, *, caption=None, bot=False, name="철수") -> Update:
    _n[0] += 1
    m = {"message_id": _n[0], "date": 0, "chat": {"id": chat, "type": "supergroup", "title": "테스트방"},
         "from": {"id": user, "is_bot": bot, "first_name": name}}
    if text is not None:
        m["text"] = text
        if text.startswith("/"):
            m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if caption is not None:
        m["caption"] = caption
        m["photo"] = [{"file_id": "p", "file_unique_id": "u", "width": 1, "height": 1}]
    return Update.model_validate({"update_id": _n[0], "message": m})


def gcb(chat, user, data) -> Update:
    _n[0] += 1
    return Update.model_validate({"update_id": _n[0], "callback_query": {
        "id": str(_n[0]), "chat_instance": "x", "data": data, "from": {"id": user, "is_bot": False, "first_name": "철수"},
        "message": {"message_id": 9, "date": 0, "chat": {"id": chat, "type": "supergroup", "title": "테스트방"},
                    "from": {"id": 1, "is_bot": True, "first_name": "b"}, "text": "x"}}})


@pytest.fixture
async def env(make_cfg, bot, tg):
    tg.bots = {ALPHA: "alpha_bot", BETA: "beta_bot"}
    cfg = make_cfg()
    db = await open_db(cfg.db_path, SCHEMA)
    box = TokenBox(KEY)
    revoked = []

    async def on_revoked(owner, username):
        revoked.append((owner, username))
        await bot.send_message(owner, f"revoked {username}")

    fleet = Fleet(db, box, api_base=tg.base, polling=False, manager_username="maker_bot", on_revoked=on_revoked)
    router._parent_router = None
    dp, _ = await assemble(cfg, bot, db, [router], Texts(TEXTS), {"fleet": fleet, "box": box, "max_bots": 3})

    class E:  # noqa: N801
        pass

    e = E()
    e.tg, e.db, e.dp, e.bot, e.fleet, e.box, e.revoked, e.cfg = tg, db, dp, bot, fleet, box, revoked, cfg
    e.settler = Settler(db, fleet)
    yield e
    await fleet.stop_all()
    await db.close()


async def say(e, uid, text):
    await e.dp.feed_update(e.bot, msg_update(uid, text))


async def press(e, uid, data):
    await e.dp.feed_update(e.bot, cb_update(uid, data))


async def make_bot(e, owner=OWNER, token=TOKEN_A):
    await press(e, owner, "add")
    await say(e, owner, token)


async def child(e, bot_id, update):
    await e.fleet.dps[bot_id].feed_update(e.fleet.bots[bot_id], update)


async def talk(e, bot_id, chat, user, text, **kw):
    await child(e, bot_id, gupd(chat, user, text, **kw))


def last(e, chat):
    return e.tg.texts_to(chat)[-1]


# ───────────── 일정 계산 ─────────────

def test_next_occurrence_is_kst_and_strictly_after():
    # 2026-10-25(일) 12:00 UTC = 21:00 KST. 월요일 21:00(KST)은 다음 날이다.
    sun_2100_kst = 1792929600
    nxt = schedule.next_occurrence(0, 21, 0, sun_2100_kst)
    assert schedule.stamp(nxt) == "10/26(월) 21:00"
    # 지금이 정확히 발표 시각이면 일주일 뒤다 (같은 시각에 두 번 발표하지 않는다)
    assert schedule.next_occurrence(6, 21, 0, sun_2100_kst) - sun_2100_kst == 7 * 86400
    assert schedule.next_occurrence(6, 21, 1, sun_2100_kst) - sun_2100_kst == 60


@pytest.mark.parametrize("text,expected", [
    ("21:00", (21, 0)), ("9:30", (9, 30)), ("21시", (21, 0)), ("오후 9시", (21, 0)), ("오후 9시 30분", (21, 30)),
    ("오전 12시", (0, 0)), ("오후 12시", (12, 0)), ("9시 5분", (9, 5)), ("21", (21, 0)),
    ("24:00", None), ("12:60", None), ("오후 13시", None), ("저녁", None), ("", None),
])
def test_parse_time(text, expected):
    assert schedule.parse_time(text) == expected


def test_schedule_text():
    assert schedule.schedule_text(0, 21, 0) == "매주 월요일 오후 9시"
    assert schedule.schedule_text(5, 9, 30) == "매주 토요일 오전 9시 30분"


def test_token_box_roundtrip_and_bad_key():
    box = TokenBox(KEY)
    sealed = box.seal(TOKEN_A)
    assert TOKEN_A not in sealed and box.open(sealed) == TOKEN_A
    with pytest.raises(RuntimeError, match="TOKEN_KEY"):
        TokenBox("짧은키")
    with pytest.raises(RuntimeError):
        TokenBox("x" * 43 + "=").open(sealed)


# ───────────── 봇 만들기 ─────────────

async def test_create_bot(env):
    await make_bot(env)
    row = await store.get_bot(env.db, ALPHA)
    assert row["owner_id"] == OWNER and row["username"] == "alpha_bot"
    assert row["min_chars"] == 5 and row["auto_on"] == 1 and (row["weekday"], row["hour"]) == (0, 21)
    assert TOKEN_A not in row["token_enc"] and env.box.open(row["token_enc"]) == TOKEN_A
    assert ALPHA in env.fleet.bots
    assert env.tg.of("deleteMessage"), "토큰이 담긴 메시지는 지워야 한다"
    assert "@alpha_bot" in last(env, OWNER) and "프라이버시" not in last(env, OWNER)


async def test_privacy_mode_warning(env):
    env.tg.read_all = False
    await make_bot(env)
    assert "프라이버시 모드가 켜져" in last(env, OWNER)
    await press(env, OWNER, f"b:{ALPHA}")
    assert "프라이버시 모드가 켜져" in last(env, OWNER)


async def test_token_rejections(env):
    await make_bot(env, token="not-a-token")
    assert await store.bots_of(env.db, OWNER) == [] and "형식" in last(env, OWNER)

    env.tg.bad_tokens.add(ALPHA)
    await say(env, OWNER, TOKEN_A)
    assert await store.bots_of(env.db, OWNER) == [] and "연결할 수 없습니다" in last(env, OWNER)
    env.tg.bad_tokens.clear()

    await say(env, OWNER, TOKEN_A)                    # 같은 대화에서 다시 보내면 성공
    assert len(await store.bots_of(env.db, OWNER)) == 1
    await make_bot(env, owner=OTHER)                  # 다른 사람이 같은 봇을 등록하려는 경우
    assert "이미 등록" in last(env, OTHER) and (await store.get_bot(env.db, ALPHA))["owner_id"] == OWNER


async def test_bot_limit_and_admin_exempt(env):
    env.tg.bots.update({333333: "c_bot", 444444: "d_bot", 555555: "e_bot"})
    for i, n in enumerate((ALPHA, BETA, 333333)):
        await make_bot(env, token=f"{n}:" + "Z" * 35)
    assert len(await store.bots_of(env.db, OWNER)) == 3
    await press(env, OWNER, "add")
    assert "최대 <b>3개</b>" in last(env, OWNER)
    for n in (444444, 555555):                        # 관리자는 제한이 없다
        await make_bot(env, owner=ADMIN, token=f"{n}:" + "Y" * 35)
    assert len(await store.bots_of(env.db, ADMIN)) == 2


# ───────────── 집계 ─────────────

async def test_counting_rules(env):
    await make_bot(env)
    for text in ("1234", "하하하", "/rank", "/순위", "／내기록", "", "    좁    "):   # 5자 미만이거나 명령어
        await talk(env, ALPHA, GROUP, 1, text)
    await talk(env, ALPHA, GROUP, 1, "12345")                        # 정확히 5자
    await talk(env, ALPHA, GROUP, 1, "봇이 보낸 긴 메시지입니다", bot=True)
    await child(env, ALPHA, gupd(GROUP, 1, caption="사진 설명입니다"))   # 사진 캡션도 센다
    rows = await store.ranking(env.db, ALPHA, GROUP, 1)
    assert [(r["user_id"], r["messages"]) for r in rows] == [(1, 2)]


async def test_min_chars_follows_setting(env):
    await make_bot(env)
    await store.update_bot(env.db, ALPHA, min_chars=2)
    await talk(env, ALPHA, GROUP, 1, "네네")
    await talk(env, ALPHA, GROUP, 1, "네")
    assert (await store.ranking(env.db, ALPHA, GROUP, 1))[0]["messages"] == 1


async def test_ranking_order_ties_and_position(env):
    await make_bot(env)
    await store.update_bot(env.db, ALPHA, min_chars=1)
    t = time.time()
    for user, at in ((1, t), (2, t + 1), (1, t + 2), (3, t + 3), (2, t + 4), (3, t + 5)):
        await store.record(env.db, ALPHA, GROUP, 1, user, f"u{user}", at)
    rows = await store.ranking(env.db, ALPHA, GROUP, 1)
    # 모두 2회. 마지막으로 2회에 도달한 시각이 이른 순: 1(t+2) → 2(t+4) → 3(t+5)
    assert [r["user_id"] for r in rows] == [1, 2, 3]
    assert await store.my_stats(env.db, ALPHA, GROUP, 1, 3) == (2, 3, 3)
    assert await store.my_stats(env.db, ALPHA, GROUP, 1, 99) == (0, 0, 3)


async def test_bots_and_chats_are_isolated(env):
    await make_bot(env)
    await make_bot(env, owner=OTHER, token=TOKEN_B)
    await talk(env, ALPHA, GROUP, 1, "알파 방 채팅입니다")
    await talk(env, ALPHA, GROUP2, 1, "알파 다른 방입니다")
    await talk(env, BETA, GROUP, 1, "베타 방 채팅입니다")
    await talk(env, BETA, GROUP, 1, "베타 방 채팅입니다 둘")
    assert (await store.ranking(env.db, ALPHA, GROUP, 1))[0]["messages"] == 1
    assert (await store.ranking(env.db, ALPHA, GROUP2, 1))[0]["messages"] == 1
    assert (await store.ranking(env.db, BETA, GROUP, 1))[0]["messages"] == 2
    await store.update_bot(env.db, BETA, min_chars=50)           # 설정은 봇마다 따로다
    await talk(env, BETA, GROUP, 1, "베타 방 채팅입니다 셋")
    await talk(env, ALPHA, GROUP, 1, "알파 방 채팅입니다 둘")
    assert (await store.ranking(env.db, BETA, GROUP, 1))[0]["messages"] == 2
    assert (await store.ranking(env.db, ALPHA, GROUP, 1))[0]["messages"] == 2


# ───────────── 명령어 ─────────────

async def test_rank_and_me_commands(env):
    await make_bot(env)
    await talk(env, ALPHA, GROUP, 1, "첫번째 채팅입니다", name="영희")
    await talk(env, ALPHA, GROUP, 1, "두번째 채팅입니다", name="영희")
    await talk(env, ALPHA, GROUP, 2, "철수의 채팅입니다", name="철수")
    for cmd in ("/순위", "/rank", "／순위", "/랭킹@alpha_bot"):
        await talk(env, ALPHA, GROUP, 2, cmd)
        out = last(env, GROUP)
        assert "영희" in out and "2회" in out and "내 기록" in out and "2위" in out, cmd
    for cmd in ("/내기록", "/me", "/내 기록"):
        await talk(env, ALPHA, GROUP, 1, cmd, name="영희")
        assert "1위" in last(env, GROUP) and "2회" in last(env, GROUP), cmd
    await talk(env, ALPHA, GROUP, 9, "/rules")
    assert "5글자 이상" in last(env, GROUP) and "월요일 오후 9시" in last(env, GROUP)


async def test_rank_empty_and_private(env):
    await make_bot(env)
    await talk(env, ALPHA, GROUP, 1, "/순위")
    assert "아직 집계된 채팅이 없습니다" in last(env, GROUP)
    await child(env, ALPHA, msg_update(7, "/rank"))
    assert "그룹 채팅방" in last(env, 7)
    await child(env, ALPHA, msg_update(7, "/start"))
    assert "@maker_bot" in last(env, 7)


async def test_admin_only_commands_are_silent_for_others(env):
    await make_bot(env)
    await talk(env, ALPHA, GROUP, 1, "채팅 하나 남깁니다")
    before = len(env.tg.texts_to(GROUP))
    await talk(env, ALPHA, GROUP, 5, "/집계")
    await talk(env, ALPHA, GROUP, 5, "/상태")
    assert len(env.tg.texts_to(GROUP)) == before            # 일반 유저에게는 아무 반응이 없다
    env.tg.members[(str(GROUP), 6)] = "administrator"
    await talk(env, ALPHA, GROUP, 6, "/집계")
    assert "중간 집계" in last(env, GROUP) and "초기화되지 않았습니다" in last(env, GROUP)
    await talk(env, ALPHA, GROUP, OWNER, "/상태")           # 봇 주인은 그룹 관리자가 아니어도 된다
    assert "봇 상태" in last(env, GROUP)
    assert (await store.get_bot(env.db, ALPHA))["round"] == 1       # 미리보기는 회차를 넘기지 않는다
    assert (await store.ranking(env.db, ALPHA, GROUP, 1))[0]["messages"] == 1


async def test_buttons(env):
    await make_bot(env)
    await talk(env, ALPHA, GROUP, 1, "버튼 확인용 채팅")
    await child(env, ALPHA, gcb(GROUP, 1, "my"))
    alert = env.tg.of("answerCallbackQuery")[-1]
    assert alert.get("show_alert") and "1위" in alert["text"]
    await child(env, ALPHA, gcb(GROUP, 1, "rank"))
    assert "TOP" in last(env, GROUP)


# ───────────── 설정 ─────────────

async def test_panel_requires_ownership(env):
    await make_bot(env)
    await press(env, OTHER, f"b:{ALPHA}")
    assert env.tg.of("answerCallbackQuery")[-1].get("show_alert")
    await press(env, OTHER, f"e:{ALPHA}:min")
    await say(env, OTHER, "50")
    assert (await store.get_bot(env.db, ALPHA))["min_chars"] == 5


async def test_edit_min_chars(env):
    await make_bot(env)
    await press(env, OWNER, f"e:{ALPHA}:min")
    for bad in ("0", "101", "열", "-3"):
        await say(env, OWNER, bad)
        assert (await store.get_bot(env.db, ALPHA))["min_chars"] == 5, bad
    await say(env, OWNER, "8")
    assert (await store.get_bot(env.db, ALPHA))["min_chars"] == 8 and "8자" in last(env, OWNER)
    await talk(env, ALPHA, GROUP, 1, "일곱글자입니다")   # 7자 → 이제 안 센다
    assert await store.ranking(env.db, ALPHA, GROUP, 1) == []


async def test_edit_schedule(env):
    await make_bot(env)
    old_next = (await store.get_bot(env.db, ALPHA))["next_settle_at"]
    await press(env, OWNER, f"wd:{ALPHA}")
    await press(env, OWNER, f"d:{ALPHA}:5")
    await press(env, OWNER, f"e:{ALPHA}:time")
    await say(env, OWNER, "25시")
    assert "읽지 못했습니다" in last(env, OWNER)
    await say(env, OWNER, "오전 9시 30분")
    row = await store.get_bot(env.db, ALPHA)
    assert (row["weekday"], row["hour"], row["minute"]) == (5, 9, 30)
    assert row["next_settle_at"] != old_next
    assert schedule.stamp(row["next_settle_at"]).endswith("(토) 09:30")
    assert "매주 토요일 오전 9시 30분" in last(env, OWNER)
    assert row["round"] == 1                                  # 일정을 바꿔도 이번 회차는 그대로


async def test_toggle_auto(env):
    await make_bot(env)
    await press(env, OWNER, f"t:{ALPHA}")
    assert (await store.get_bot(env.db, ALPHA))["auto_on"] == 0
    assert "OFF" in last(env, OWNER)
    await env.db.execute("UPDATE bots SET auto_on=0, next_settle_at=1 WHERE bot_id=?", (ALPHA,))   # 꺼져 있는 동안 시각이 한참 지났다고 치자
    await env.db.commit()
    await press(env, OWNER, f"t:{ALPHA}")
    row = await store.get_bot(env.db, ALPHA)
    assert row["auto_on"] == 1 and row["next_settle_at"] > time.time()   # 켜면 과거 시각이 아니라 다음 일정부터


async def test_edit_prizes_and_qualify(env):
    await make_bot(env)
    await press(env, OWNER, f"e:{ALPHA}:prizes")
    await say(env, OWNER, "치킨 기프티콘\n커피 쿠폰\n\n")
    assert store.prize_list(await store.get_bot(env.db, ALPHA)) == ["치킨 기프티콘", "커피 쿠폰"]
    await press(env, OWNER, f"e:{ALPHA}:prizes")
    await say(env, OWNER, "\n".join(str(i) for i in range(11)))
    assert len(store.prize_list(await store.get_bot(env.db, ALPHA))) == 2
    await press(env, OWNER, f"e:{ALPHA}:qualify")
    await say(env, OWNER, "300")
    assert (await store.get_bot(env.db, ALPHA))["qualify"] == 300
    await press(env, OWNER, f"e:{ALPHA}:prizes")
    await say(env, OWNER, "없음")
    assert store.prize_list(await store.get_bot(env.db, ALPHA)) == []


async def test_edit_name_calls_set_my_name(env):
    await make_bot(env)
    await press(env, OWNER, f"e:{ALPHA}:name")
    await say(env, OWNER, "수다왕 봇")
    assert env.tg.of("setMyName")[-1]["name"] == "수다왕 봇"
    assert (await store.get_bot(env.db, ALPHA))["name"] == "수다왕 봇"
    await press(env, OWNER, f"e:{ALPHA}:name")
    await say(env, OWNER, "가" * 65)
    assert (await store.get_bot(env.db, ALPHA))["name"] == "수다왕 봇"


async def test_cancel_clears_input_state(env):
    await make_bot(env)
    await press(env, OWNER, f"e:{ALPHA}:min")
    await say(env, OWNER, "/cancel")
    await say(env, OWNER, "77")
    assert (await store.get_bot(env.db, ALPHA))["min_chars"] == 5


async def test_replace_token_and_delete(env):
    await make_bot(env)
    await press(env, OWNER, f"e:{ALPHA}:token")
    await say(env, OWNER, TOKEN_B)                              # 다른 봇의 토큰은 거절
    assert "다른 봇" in last(env, OWNER)
    new = f"{ALPHA}:" + "N" * 35
    await say(env, OWNER, new)
    assert env.box.open((await store.get_bot(env.db, ALPHA))["token_enc"]) == new

    await talk(env, ALPHA, GROUP, 1, "삭제 전에 남긴 채팅")
    await press(env, OWNER, f"del:{ALPHA}")
    assert await store.get_bot(env.db, ALPHA) is not None       # 확인 전에는 지우지 않는다
    await press(env, OWNER, f"delok:{ALPHA}")
    assert await store.get_bot(env.db, ALPHA) is None and ALPHA not in env.fleet.bots
    assert await store.ranking(env.db, ALPHA, GROUP, 1) == []


# ───────────── 주간 발표 ─────────────

async def fill(e, bot_id, chat, plan: dict[int, int]):
    for user, n in plan.items():
        for k in range(n):
            await talk(e, bot_id, chat, user, f"{user}번 사람의 채팅 {k}", name=f"사람{user}")


async def due(e, bot_id=ALPHA) -> float:
    return (await store.get_bot(e.db, bot_id))["next_settle_at"] + 1


async def test_settle_announces_pins_and_resets(env):
    await make_bot(env)
    await fill(env, ALPHA, GROUP, {1: 3, 2: 5, 3: 1})
    await env.settler.tick(await due(env) - 100)               # 아직 시각 전이면 아무 일도 없다
    assert env.tg.of("pinChatMessage") == []
    await env.settler.tick(await due(env))
    out = last(env, GROUP)
    assert "1회차 채팅 순위 발표" in out and out.index("사람2") < out.index("사람1") < out.index("사람3")
    assert "5회" in out and "초기화" in out
    assert len(env.tg.of("pinChatMessage")) == 1
    row = await store.get_bot(env.db, ALPHA)
    assert row["round"] == 2 and row["next_settle_at"] > time.time()
    assert await store.ranking(env.db, ALPHA, GROUP, 2) == []        # 새 회차는 0에서 시작
    assert len(await store.ranking(env.db, ALPHA, GROUP, 1)) == 3    # 지난 기록은 남는다
    await talk(env, ALPHA, GROUP, 1, "새 회차 첫 채팅")
    assert (await store.ranking(env.db, ALPHA, GROUP, 2))[0]["messages"] == 1
    await env.settler.tick(await due(env) - 1000)                    # 큐는 비었으니 다시 보내지 않는다
    assert len(env.tg.of("pinChatMessage")) == 1


async def winners_of(e, bot_id=ALPHA):
    async with e.db.execute("SELECT rank, name, prize FROM winners WHERE bot_id=? ORDER BY rank", (bot_id,)) as cur:
        return [(r["rank"], r["name"], r["prize"]) for r in await cur.fetchall()]


async def test_settle_prizes_and_qualify(env):
    await make_bot(env)
    await store.update_bot(env.db, ALPHA, prizes="5만원 쿠폰\n3만원 쿠폰\n1만원 쿠폰", qualify=3)
    await fill(env, ALPHA, GROUP, {1: 6, 2: 4, 3: 2, 4: 3})        # 3번은 자격(3회) 미달
    await env.settler.tick(await due(env))
    assert await winners_of(env) == [(1, "사람1", "5만원 쿠폰"), (2, "사람2", "3만원 쿠폰"), (3, "사람4", "1만원 쿠폰")]
    out = last(env, GROUP)
    assert out.split("수상자")[1].count("쿠폰") == 3 and "사람3" in out.split("수상자")[0]   # 사람3은 순위표에만 있다


async def test_settle_fewer_qualified_than_prizes(env):
    await make_bot(env)
    await store.update_bot(env.db, ALPHA, prizes="5만원 쿠폰\n3만원 쿠폰\n1만원 쿠폰", qualify=4)
    await fill(env, ALPHA, GROUP, {1: 6, 2: 4, 3: 2})
    await env.settler.tick(await due(env))
    assert await winners_of(env) == [(1, "사람1", "5만원 쿠폰"), (2, "사람2", "3만원 쿠폰")]
    assert "1만원 쿠폰" not in last(env, GROUP)


async def test_settle_no_prizes_means_ranking_only(env):
    await make_bot(env)
    await fill(env, ALPHA, GROUP, {1: 2})
    await env.settler.tick(await due(env))
    assert "수상자" not in last(env, GROUP) and await winners_of(env) == []


async def test_settle_nobody_qualifies(env):
    await make_bot(env)
    await store.update_bot(env.db, ALPHA, prizes="상품", qualify=100)
    await fill(env, ALPHA, GROUP, {1: 2})
    await env.settler.tick(await due(env))
    assert "수상자가 없습니다" in last(env, GROUP)


async def test_settle_skips_quiet_chats_and_auto_off(env):
    await make_bot(env)
    await store.upsert_chat(env.db, ALPHA, GROUP2, "조용한 방")
    await fill(env, ALPHA, GROUP, {1: 1})
    await env.settler.tick(await due(env))
    assert env.tg.texts_to(GROUP2) == [] and len(env.tg.texts_to(GROUP)) >= 1

    await make_bot(env, owner=OTHER, token=TOKEN_B)
    await store.set_auto(env.db, BETA, False)
    await fill(env, BETA, GROUP, {1: 1})
    await env.settler.tick(await due(env, BETA) + 10**7)
    assert (await store.get_bot(env.db, BETA))["round"] == 1       # 꺼진 봇은 발표하지 않고 계속 쌓는다


async def test_settle_each_bot_uses_own_schedule(env):
    await make_bot(env)
    await make_bot(env, owner=OTHER, token=TOKEN_B)
    await store.set_schedule(env.db, BETA, weekday=3, hour=8, minute=15)
    await fill(env, ALPHA, GROUP, {1: 1})
    await fill(env, BETA, GROUP2, {1: 1})
    a, b = await due(env, ALPHA), await due(env, BETA)
    assert a != b
    first = min(a, b)
    await env.settler.tick(first)
    rounds = {r["bot_id"]: r["round"] for r in await store.active_bots(env.db)}
    assert sorted(rounds.values()) == [1, 2]


async def test_missed_settle_catches_up_once(env):
    await make_bot(env)
    await fill(env, ALPHA, GROUP, {1: 1})
    way_later = (await due(env)) + 30 * 86400                        # 한 달 동안 꺼져 있었다
    await env.settler.tick(way_later)
    await env.settler.tick(way_later)
    assert (await store.get_bot(env.db, ALPHA))["round"] == 2        # 밀린 발표는 한 번만
    assert len(env.tg.of("pinChatMessage")) == 1
    assert (await store.get_bot(env.db, ALPHA))["next_settle_at"] > way_later


async def test_failed_announce_is_retried_not_lost(env):
    await make_bot(env)
    await fill(env, ALPHA, GROUP, {1: 2})
    env.tg.retry_once.add(GROUP)                                     # 처음 보낼 때 429
    await env.settler.tick(await due(env))
    assert len(await store.queued(env.db)) == 1 and (await store.get_bot(env.db, ALPHA))["round"] == 2
    await env.settler.tick(time.time())
    assert await store.queued(env.db) == [] and "1회차" in last(env, GROUP)


async def test_announce_survives_restart(env):
    await make_bot(env)
    await fill(env, ALPHA, GROUP, {1: 2})
    bot = await store.get_bot(env.db, ALPHA)
    assert await store.advance_round(env.db, bot, await due(env))     # 큐에 넣기만 하고 '죽었다'
    assert not await store.advance_round(env.db, bot, await due(env))  # 같은 회차를 두 번 넘기지 않는다
    assert len(env.tg.of("sendMessage")) < 99 and await store.queued(env.db)
    fresh = Settler(env.db, env.fleet)                                # 재시작 후 새 Settler
    await fresh.tick(time.time())
    assert "1회차" in last(env, GROUP) and await store.queued(env.db) == []


async def test_kicked_group_is_deactivated(env):
    await make_bot(env)
    await fill(env, ALPHA, GROUP, {1: 1})
    env.tg.forbidden.add(GROUP)
    await env.settler.tick(await due(env))
    assert await store.queued(env.db) == []
    async with env.db.execute("SELECT active FROM chats WHERE chat_id=?", (GROUP,)) as cur:
        assert (await cur.fetchone())["active"] == 0


async def test_revoked_token_stops_bot_and_tells_owner(env):
    await make_bot(env)
    await fill(env, ALPHA, GROUP, {1: 1})
    env.tg.bad_tokens.add(ALPHA)
    await env.settler.tick(await due(env))
    assert (await store.get_bot(env.db, ALPHA))["status"] == "revoked"
    assert ALPHA not in env.fleet.bots and env.revoked == [(OWNER, "alpha_bot")]
    assert "revoked alpha_bot" in last(env, OWNER)
    env.tg.bad_tokens.clear()                                          # 새 토큰으로 되살리기
    await press(env, OWNER, f"e:{ALPHA}:token")
    await say(env, OWNER, f"{ALPHA}:" + "R" * 35)
    assert (await store.get_bot(env.db, ALPHA))["status"] == "active" and ALPHA in env.fleet.bots


async def test_health_check_detects_revoked_token(env):
    await make_bot(env)
    await make_bot(env, owner=OTHER, token=TOKEN_B)
    env.tg.bad_tokens.add(BETA)
    await env.fleet.health()
    assert (await store.get_bot(env.db, BETA))["status"] == "revoked"
    assert (await store.get_bot(env.db, ALPHA))["status"] == "active" and ALPHA in env.fleet.bots


async def test_bot_membership_events(env):
    await make_bot(env)
    ev = lambda status: Update.model_validate({"update_id": 5, "my_chat_member": {   # noqa: E731
        "chat": {"id": GROUP, "type": "supergroup", "title": "새 방"}, "from": {"id": 1, "is_bot": False, "first_name": "a"},
        "date": 0, "old_chat_member": {"status": "left", "user": {"id": ALPHA, "is_bot": True, "first_name": "b"}},
        "new_chat_member": {"status": status, "user": {"id": ALPHA, "is_bot": True, "first_name": "b"}}}})
    await child(env, ALPHA, ev("member"))
    assert await store.chat_count(env.db, ALPHA) == 1
    await child(env, ALPHA, ev("left"))
    assert await store.chat_count(env.db, ALPHA) == 0


# ───────────── 실제 폴링 / 시작·종료 ─────────────

async def test_real_polling_counts_messages_and_restores_on_restart(env):
    await make_bot(env)
    live = Fleet(env.db, env.box, api_base=env.tg.base, polling=True)
    await live.start_all()                                             # DB에서 복구해 폴링 시작
    try:
        assert ALPHA in live.tasks
        env.tg.queued[ALPHA] = [gupd(GROUP, 1, "폴링으로 들어온 채팅").model_dump(mode="json", exclude_none=True)]
        for _ in range(60):
            if await store.ranking(env.db, ALPHA, GROUP, 1):
                break
            await asyncio.sleep(0.05)
        assert (await store.ranking(env.db, ALPHA, GROUP, 1))[0]["messages"] == 1
    finally:
        await live.stop_all()
    assert live.tasks == {} and live.bots == {}


async def test_start_all_skips_revoked_and_survives_one_bad_row(env):
    await make_bot(env)
    await make_bot(env, owner=OTHER, token=TOKEN_B)
    await store.update_bot(env.db, BETA, status="revoked")
    other = Fleet(env.db, env.box, api_base=env.tg.base, polling=False)
    await other.start_all()
    assert set(other.bots) == {ALPHA}
    await other.stop_all()
    await store.update_bot(env.db, ALPHA, token_enc="깨진 값")
    broken = Fleet(env.db, env.box, api_base=env.tg.base, polling=False)
    await broken.start_all()                                             # 예외 없이 넘어간다
    assert broken.bots == {}


async def test_start_menu_and_unknown_text(env):
    await say(env, OWNER, "/start")
    assert "봇 만들기" in json.loads(env.tg.of("sendMessage")[-1]["reply_markup"])["inline_keyboard"][0][0]["text"]
    await say(env, OWNER, "아무 말")
    assert "메뉴에서 선택" in last(env, OWNER)
    await press(env, OWNER, "my")
    assert "아직 만든 봇이 없습니다" in last(env, OWNER)


async def test_startup_and_shutdown_hooks(env):
    import dataclasses

    from bot import services

    await make_bot(env)
    with pytest.raises(RuntimeError, match="TOKEN_KEY"):
        await services.on_startup(env.bot, env.db, env.cfg)
    cfg = dataclasses.replace(env.cfg, env={"TOKEN_KEY": KEY, "MAX_BOTS_PER_USER": "5"}, api_base=env.tg.base)
    extra = await services.on_startup(env.bot, env.db, cfg)
    try:
        assert extra["max_bots"] == 5 and ALPHA in extra["fleet"].bots      # 저장된 봇이 다시 떠 있다
        assert extra["fleet"].manager_username == "tip_test_bot"
    finally:
        await services.on_shutdown(extra)
    assert extra["fleet"].bots == {} and extra["settler_task"].done()
