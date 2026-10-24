import io
import json
import re
import subprocess
from pathlib import Path

import pytest
from aiohttp import web
from PIL import Image, ImageDraw

from bot import kakao, stickers
from bot.handlers import router
from bot.kakao import KakaoClient, Session
from main import SCHEMA
from bot.services import on_startup
from bot.texts import TEXTS
from core.bot_runner import assemble
from core.db import open_db
from core.texts import Texts
from testkit.fixtures import ADMIN, msg_update
from testkit.fake_api import BOT_USERNAME

KAKAO = Path(__file__).resolve().parent.parent


def anim_webp(n=6, ms=200) -> bytes:
    frames = []
    for i in range(n):
        im = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((10 + i * 20, 60, 110 + i * 20, 160), fill=(255, 0, 0, 255))
        frames.append(im)
    b = io.BytesIO()
    frames[0].save(b, "WEBP", save_all=True, append_images=frames[1:], duration=ms, loop=0, lossless=True)
    return b.getvalue()


def still_png() -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", (360, 360), (0, 128, 255, 255)).save(b, "PNG")
    return b.getvalue()


class FakeKakao:
    """e.kakao.com API와 CDN 흉내."""

    def __init__(self):
        self.packs = {}   # key -> (title, [bytes...])
        self.alias = {}   # 다른 링크 형태: alias key -> 원래 key (같은 이미지 URL을 돌려준다)
        self.hits = 0

    async def start(self):
        app = web.Application()
        app.router.add_get("/api/items/{key}", self.api)
        app.router.add_get("/img/{key}/{i}", self.img)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"

    async def api(self, req):
        self.hits += 1
        key = req.match_info["key"]
        self.last_cookie = req.headers.get("Cookie")
        if self.alias.get(key, key) not in self.packs:
            return web.json_response({"status": 404}, status=404)
        real = self.alias.get(key, key)
        title, imgs = self.packs[real]
        return web.json_response({"hero": {"title": title}, "contents": {"items": [
            {"animatedUrl": f"{self.base}/img/{real}/{i}"} for i in range(len(imgs))]}})

    async def img(self, req):
        _, imgs = self.packs[req.match_info["key"]]
        return web.Response(body=imgs[int(req.match_info["i"])])


@pytest.fixture
async def kk(monkeypatch):
    k = FakeKakao()
    await k.start()
    monkeypatch.setattr(kakao, "is_kakao_host", lambda u: u.startswith("http://127.0.0.1"))
    yield k
    await k.runner.cleanup()


@pytest.fixture
def session_file(tmp_path):
    p = tmp_path / "session.json"
    p.write_text(json.dumps({"cookies": [{"name": "k", "value": "v", "domain": ".kakao.com", "expires": -1}]}))
    return p


async def make_bot(make_cfg, bot, kk, session_path, channels=()):
    cfg = make_cfg(channels=channels)
    cfg = cfg.__class__(**{**cfg.__dict__, "env": {"KAKAO_SESSION_PATH": str(session_path)}})
    db = await open_db(cfg.db_path, SCHEMA)
    data = await on_startup(bot, db, cfg)
    data["kakao"] = KakaoClient(Session(session_path), api_url=kk.base + "/api/items/{key}")
    router._parent_router = None
    dp, _ = await assemble(cfg, bot, db, [router], Texts(TEXTS), data)
    return dp, db, cfg


def test_set_name_and_title_rules():
    n = stickers.make_set_name(42, "Pigeon-Boy_2", "Tip_Bot")
    assert re.fullmatch(r"[a-z][a-z0-9_]*_by_Tip_Bot", n) and "__" not in n
    long = stickers.make_set_name(42, "x" * 200, "Tip_Bot")
    assert len(long) <= 64 and long.endswith("_by_Tip_Bot") and "__" not in long
    assert stickers.make_set_name(42, "x" * 200, "Tip_Bot") == long  # 결정적
    assert stickers.make_title("귀여운 비둘기") == "TIPㅣ귀여운 비둘기"
    assert len(stickers.make_title("가" * 100)) == 64 and stickers.make_title("가" * 100).startswith("TIPㅣ")


def test_parse_link():
    assert kakao.parse_link("보자 https://e.kakao.com/t/pigeon-boy?x=1 !") == "pigeon-boy"
    assert kakao.parse_link("https://emoticon.kakao.com/items/AbC_12-x") == "AbC_12-x"
    assert kakao.parse_link("그냥 글") is None
    assert not kakao.is_kakao_host("https://evil.com/a.png") and kakao.is_kakao_host("https://t1.kakaocdn.net/a")


async def test_animated_pack_becomes_video_sticker_set(make_cfg, bot, tg, kk, session_file):
    kk.packs["pigeon-boy"] = ("귀여운 비둘기", [anim_webp(), anim_webp(8)])
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(7, "여기 https://e.kakao.com/t/pigeon-boy"))
    name = f"tip_7_pigeon_boy_by_{BOT_USERNAME}"
    assert name in tg.sets
    s = tg.sets[name]
    assert s["title"] == "TIPㅣ귀여운 비둘기"
    assert len(s["stickers"]) == 2 and all(x["format"] == "video" for x in s["stickers"])
    assert all(sz <= 256 * 1024 for sz in s["files"].values())
    assert kk.last_cookie == "k=v"
    assert f"https://t.me/addstickers/{name}" in tg.texts_to(7)[-1]

    # 같은 링크를 다시 보내면 새로 만들지 않고 링크만 준다
    await dp.feed_update(bot, msg_update(7, "https://e.kakao.com/t/pigeon-boy"))
    assert len(tg.of("createNewStickerSet")) == 1
    assert f"https://t.me/addstickers/{name}" in tg.texts_to(7)[-1]

    # 사용자가 세트를 지웠으면 다시 만든다
    del tg.sets[name]
    await dp.feed_update(bot, msg_update(7, "https://e.kakao.com/t/pigeon-boy"))
    assert len(tg.of("createNewStickerSet")) == 2 and name in tg.sets
    await db.close()


async def test_static_pack_and_over_50_stickers(make_cfg, bot, tg, kk, session_file):
    kk.packs["many"] = ("정지 팩", [still_png()] * 55)
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(8, "https://e.kakao.com/t/many"))
    s = tg.sets[f"tip_8_many_by_{BOT_USERNAME}"]
    assert len(s["stickers"]) == 55 and s["stickers"][0]["format"] == "static"   # 50 + addStickerToSet 5
    assert len(tg.of("addStickerToSet")) == 5
    await db.close()


async def test_mixed_pack_keeps_animated_only(make_cfg, bot, tg, kk, session_file):
    kk.packs["mix"] = ("섞인 팩", [anim_webp(), still_png(), anim_webp()])
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(9, "https://e.kakao.com/t/mix"))
    assert len(tg.sets[f"tip_9_mix_by_{BOT_USERNAME}"]["stickers"]) == 2
    assert "정지 이미지 1개" in tg.texts_to(9)[-1]
    await db.close()


async def test_without_session_refuses_and_alerts_admin_once(make_cfg, bot, tg, kk, tmp_path):
    kk.packs["p"] = ("팩", [still_png()])     # 로그인 없이 받으면 움직이는 것도 정지로 내려온다
    dp, db, _ = await make_bot(make_cfg, bot, kk, tmp_path / "missing.json")
    await dp.feed_update(bot, msg_update(3, "https://e.kakao.com/t/p"))
    await dp.feed_update(bot, msg_update(3, "https://e.kakao.com/t/p"))
    assert not tg.sets
    assert "점검 중" in tg.texts_to(3)[-1]
    alerts = [t for t in tg.texts_to(ADMIN) if "세션" in t]
    assert len(alerts) == 1
    await db.close()


async def test_bad_inputs(make_cfg, bot, tg, kk, session_file):
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(4, "안녕"))
    assert "링크" in tg.texts_to(4)[-1]
    await dp.feed_update(bot, msg_update(4, "https://e.kakao.com/t/nope"))
    assert "찾을 수 없습니다" in tg.texts_to(4)[-1] and not tg.sets
    await db.close()


async def test_partial_failure_skips_bad_image(make_cfg, bot, tg, kk, session_file):
    kk.packs["bad"] = ("깨짐", [anim_webp(), b"RIFF\x00\x00\x00\x00WEBPANIMgarbage" + b"\0" * 40])
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(6, "https://e.kakao.com/t/bad"))
    assert len(tg.sets[f"tip_6_bad_by_{BOT_USERNAME}"]["stickers"]) == 1
    assert "<b>1개</b>는 제외" in tg.texts_to(6)[-1]
    await db.close()


def test_login_url_detection():
    from bot.login import is_logged_in_url

    assert is_logged_in_url("https://e.kakao.com/")
    assert not is_logged_in_url("https://accounts.kakao.com/login?continue=x")
    assert not is_logged_in_url("https://example.com/kakao.com/")


async def test_login_hint_is_not_error():
    """'TIP ...' 안내 문구는 로그인 에러로 취급하지 않는다."""
    import http.server
    import threading

    from playwright.async_api import async_playwright

    from bot import login

    html = b'<html><body><p class="txt_error">TIP hint</p><p class="txt_error">wrong password</p></body></html>'
    only_hint = b'<html><body><p class="txt_error">TIP hint</p></body></html>'
    pages = {"/a": html, "/b": only_hint}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(pages[self.path])
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async with async_playwright() as pw:
            b = await pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
            pg = await b.new_page()
            await pg.goto(f"http://127.0.0.1:{srv.server_port}/b")
            assert await login.error_text(pg) == ""
            await pg.goto(f"http://127.0.0.1:{srv.server_port}/a")
            assert await login.error_text(pg) == "wrong password"
            await b.close()
    except Exception as e:  # noqa: BLE001
        if "Executable doesn't exist" in str(e) or "executable" in str(e).lower():
            pytest.skip("브라우저를 쓸 수 없는 환경")
        raise
    finally:
        srv.shutdown()


async def test_login_dismisses_password_change_interstitial():
    import http.server
    import threading

    from playwright.async_api import async_playwright

    from bot import login

    html = (b'<html><body><p>Kakao \xea\xb0\x9c\xec\x9d\xb8\xec\xa0\x95\xeb\xb3\xb4 \xeb\xb9\x84\xeb\xb0\x80\xeb\xb2\x88\xed\x98\xb8\xeb\xa5\xbc \xeb\xb3\x80\xea\xb2\xbd\xed\x95\xb4 \xec\xa3\xbc\xec\x84\xb8\xec\x9a\x94</p>'
            b'<button>\xed\x99\x95\xec\x9d\xb8</button>'
            b'<button onclick="document.body.innerHTML=\'done\'">\xeb\x8b\xa4\xec\x9d\x8c\xec\x97\x90 \xeb\xb3\x80\xea\xb2\xbd\xed\x95\x98\xea\xb8\xb0</button></body></html>')

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(html)
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async with async_playwright() as pw:
            try:
                b = await pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
            except Exception:  # noqa: BLE001
                pytest.skip("브라우저를 쓸 수 없는 환경")
            pg = await b.new_page()
            await pg.goto(f"http://127.0.0.1:{srv.server_port}/")
            assert await login.dismiss_interstitial(pg) is True
            assert (await pg.inner_text("body")).strip() == "done"   # '확인'이 아니라 '다음에 변경하기'를 눌렀다
            assert await login.dismiss_interstitial(pg) is False
            await b.close()
    finally:
        srv.shutdown()


def test_login_is_real_error():
    from bot.login import is_real_error

    assert is_real_error("비밀번호가 올바르지 않습니다.")
    assert not is_real_error("TIP 카카오메일이 있다면 메일 아이디만 입력해 보세요.")
    assert not is_real_error("/login/?continue=https%3A%2F%2Fe.kakao.com%2F")
    assert not is_real_error("https://accounts.kakao.com/login")
    assert not is_real_error("   ")


async def test_start_and_help_are_formal_rich_and_have_shop_link(make_cfg, bot, tg, kk, session_file):
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(21, "/start"))
    start = tg.of("sendMessage")[-1]
    t = start["text"]
    assert "21" == start["chat_id"] and "테스터님, 환영합니다" in t
    assert "https://e.kakao.com/" in t                                  # 이모티콘샵 링크
    assert "<b>" in t and "<blockquote>" in t and "<code>" in t and "<u>" in t   # 굵게·인용·코드·밑줄
    assert "TIPㅣ이모티콘이름" in t and len(t) < 4096
    assert "해요" not in t and "주세요" not in t                        # 합쇼체
    assert json.loads(start["reply_markup"])["inline_keyboard"][0][0]["url"] == "https://e.kakao.com/"
    assert json.loads(start["link_preview_options"])["is_disabled"] is True
    async with db.execute("SELECT COUNT(*) FROM users WHERE user_id=21") as c:
        assert (await c.fetchone())[0] == 1                            # /start 등록은 유지
    await dp.feed_update(bot, msg_update(21, "/help"))
    h = tg.of("sendMessage")[-1]["text"]
    assert "자주 묻는 질문" in h and len(h) < 4096
    await db.close()


async def test_download_returns_whole_body_even_when_it_arrives_in_chunks(monkeypatch):
    """큰 이미지는 여러 조각으로 도착한다. 첫 조각만 읽으면 잘린 파일이 되어 디코더가 못 연다."""
    import asyncio

    data = __import__("os").urandom(200_000)   # 여러 조각으로 나뉘어 도착하는 크기

    async def slow(request):
        resp = web.StreamResponse()
        resp.content_length = len(data)
        await resp.prepare(request)
        for i in range(0, len(data), 4096):
            await resp.write(data[i : i + 4096])
            await asyncio.sleep(0.01)
        return resp

    app = web.Application()
    app.router.add_get("/big", slow)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    monkeypatch.setattr(kakao, "is_kakao_host", lambda u: u.startswith("http://127.0.0.1"))
    try:
        assert len(data) > 4096 * 2
        got = await KakaoClient(Session(None)).download(f"http://127.0.0.1:{runner.addresses[0][1]}/big")
        assert got == data
    finally:
        await runner.cleanup()


async def test_channel_gate_shows_join_and_check_buttons_then_resumes(make_cfg, bot, tg, kk, session_file):
    from core.config import Channel

    kk.packs["gate"] = ("게이트", [anim_webp()])
    ch = Channel(chat_id="@GGULLTIP", url="https://t.me/GGULLTIP", title="📢 채널 가입하기")
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file, channels=[ch])

    await dp.feed_update(bot, msg_update(31, "https://e.kakao.com/t/gate"))
    m = tg.of("sendMessage")[-1]
    kb = json.loads(m["reply_markup"])["inline_keyboard"]
    assert kb[0][0] == {"text": "📢 채널 가입하기", "url": "https://t.me/GGULLTIP"}
    assert kb[1][0] == {"text": "✅ 가입을 완료했습니다", "callback_data": "gate:check"}
    assert not tg.sets                                            # 가입 전에는 변환하지 않는다

    from testkit.fixtures import cb_update
    await dp.feed_update(bot, cb_update(31, "gate:check"))        # 아직 미가입
    assert not tg.sets
    tg.members[("@GGULLTIP", 31)] = "member"
    await dp.feed_update(bot, cb_update(31, "gate:check"))        # 가입 후 확인 → 처음 요청을 이어서 처리
    assert f"tip_31_gate_by_{BOT_USERNAME}" in tg.sets
    await db.close()


def test_ecosystem_config():
    out = subprocess.run(
        ["node", "-e", "console.log(JSON.stringify(require('./ecosystem.config.js').apps))"],
        cwd=KAKAO, capture_output=True, text=True,
    )
    apps = json.loads(out.stdout)
    assert [a["name"] for a in apps] == ["kakao"]
    assert apps[0]["script"] == "main.py" and apps[0]["interpreter"] == "python3"
    assert (KAKAO / apps[0]["script"]).exists()




async def test_existing_set_is_returned_immediately_without_calling_kakao(make_cfg, bot, tg, kk, session_file):
    kk.packs["p"] = ("팩", [anim_webp()])
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(41, "https://e.kakao.com/t/p"))
    hits, creates = kk.hits, len(tg.of("createNewStickerSet"))
    before = len(tg.of("sendMessage"))
    await dp.feed_update(bot, msg_update(41, "https://e.kakao.com/t/p"))
    assert kk.hits == hits                                        # 카카오에 다시 접속하지 않는다
    assert len(tg.of("createNewStickerSet")) == creates           # 새로 만들지 않는다
    assert len(tg.of("sendMessage")) == before + 1                # 확인 중 메시지 없이 답 하나로 끝
    assert "이미 변환해 두신" in tg.texts_to(41)[-1]
    await db.close()


async def test_other_users_get_the_existing_set(make_cfg, bot, tg, kk, session_file):
    kk.packs["shared"] = ("공유", [anim_webp()])
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(51, "https://e.kakao.com/t/shared"))
    name = f"tip_51_shared_by_{BOT_USERNAME}"
    await dp.feed_update(bot, msg_update(52, "https://e.kakao.com/t/shared"))
    assert len(tg.of("createNewStickerSet")) == 1
    assert f"https://t.me/addstickers/{name}" in tg.texts_to(52)[-1]
    await db.close()


async def test_same_emoticon_with_different_link_form_is_recognized(make_cfg, bot, tg, kk, session_file):
    kk.packs["pigeon"] = ("비둘기", [anim_webp(), anim_webp(8)])
    kk.alias["AbC123"] = "pigeon"                                 # emoticon.kakao.com/items/AbC123 형태
    dp, db, _ = await make_bot(make_cfg, bot, kk, session_file)
    await dp.feed_update(bot, msg_update(61, "https://e.kakao.com/t/pigeon"))
    await dp.feed_update(bot, msg_update(61, "https://emoticon.kakao.com/items/AbC123"))
    assert len(tg.of("createNewStickerSet")) == 1
    assert "이미 변환해 두신" in tg.texts_to(61)[-1]
    await db.close()


async def test_migration_adds_fingerprint_to_old_db(make_cfg, tmp_path):
    import aiosqlite

    from bot.services import migrate

    db = await aiosqlite.connect(tmp_path / "old.db")
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE sticker_sets (user_id INTEGER, key TEXT, set_name TEXT, title TEXT, fmt TEXT, "
                     "total INTEGER, status TEXT, created_at INTEGER, PRIMARY KEY (user_id, key))")
    await db.execute("INSERT INTO sticker_sets VALUES (1,'k','n','t','video',3,'complete',0)")
    await migrate(db)
    await migrate(db)                                             # 두 번 실행해도 안전
    async with db.execute("SELECT fingerprint FROM sticker_sets") as c:
        assert (await c.fetchone())[0] is None
    await db.close()
