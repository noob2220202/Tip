"""관리 봇(봇을 만들고 설정하는 봇)의 문구.

kakao·boost와 같이 합쇼체에 이모지·굵게·인용을 쓴다. (docs/STYLE_GUIDE.md의 해요체·절제 규칙에 대한 이 봇 한정 예외)
사용자가 정한 값(이름, 경품 문구 등)은 호출하는 쪽에서 esc()로 감싼다.
"""
from __future__ import annotations

from html import escape as esc

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup as Kb

from bot import child_texts, schedule, store

TEXTS: dict[str, list[str]] = {
    "start": [
        "👋 <b>채팅 순위 봇 메이커</b>에 오신 것을 환영합니다.\n\n"
        "<blockquote>내 그룹의 채팅 횟수를 세어 <b>매주 순위를 발표</b>하는 <b>나만의 봇</b>을 만들 수 있습니다.\n"
        "봇 이름 · 집계 최소 글자 · 발표 요일과 시간 · 경품까지 직접 정하십시오.</blockquote>\n\n"
        "아래 버튼으로 시작해 주십시오. 👇",
    ],
    "help": [
        "❓ <b>사용 방법</b>\n\n"
        "<blockquote>1️⃣ <b>BotFather</b>에서 새 봇을 만들고 <b>토큰</b>을 받습니다.\n"
        "2️⃣ 여기서 <b>➕ 봇 만들기</b>를 누르고 토큰을 보냅니다.\n"
        "3️⃣ BotFather에서 <code>/setprivacy</code> → 내 봇 → <b>Disable</b> 로 바꿉니다.\n"
        "4️⃣ 내 봇을 <b>그룹에 초대</b>하고 <b>관리자</b>로 지정합니다.\n"
        "5️⃣ 이후 설정은 <b>🤖 내 봇</b>에서 바꾸실 수 있습니다.</blockquote>\n\n"
        "명령어: <b>/menu</b> 메뉴 · <b>/cancel</b> 입력 취소",
    ],
}

ADD_GUIDE = (
    "➕ <b>봇 만들기</b>\n\n"
    "<blockquote>1️⃣ <a href=\"https://t.me/BotFather\">@BotFather</a> 에서 <code>/newbot</code> 으로 봇을 만듭니다.\n"
    "2️⃣ 받은 <b>토큰</b>을 이 대화에 그대로 보내 주십시오.</blockquote>\n\n"
    "🔒 토큰은 <b>암호화해서 저장</b>하며, 보내신 메시지는 바로 삭제합니다.\n"
    "취소는 /cancel"
)

LIMIT = "⚠️ 만들 수 있는 봇은 최대 <b>{limit}개</b>입니다. 쓰지 않는 봇을 삭제한 뒤 다시 시도해 주십시오."
BAD_TOKEN = "❌ 토큰 형식이 올바르지 않습니다. BotFather가 준 <code>123456:ABC...</code> 형태 그대로 보내 주십시오."
DEAD_TOKEN = "❌ 이 토큰으로는 봇에 연결할 수 없습니다. 폐기됐거나 잘못 복사된 토큰일 수 있습니다. 다시 확인해 주십시오."
DUP_TOKEN = "⚠️ 이미 등록된 봇입니다. 같은 봇은 한 번만 만들 수 있습니다."
WRONG_BOT = "⚠️ 기존 봇과 다른 봇의 토큰입니다. <b>@{username}</b> 의 새 토큰을 보내 주십시오."
NO_BOTS = "🤖 아직 만든 봇이 없습니다. <b>➕ 봇 만들기</b>로 시작해 보십시오."
NOT_FOUND = "해당 봇을 찾을 수 없습니다."
CANCELLED = "취소했습니다."
UNKNOWN = "메뉴에서 선택해 주십시오. 👇"
PRIVACY_WARN = (
    "\n\n⚠️ <b>프라이버시 모드가 켜져 있습니다.</b>\n"
    "BotFather에서 <code>/setprivacy</code> → 이 봇 → <b>Disable</b> 로 바꾸거나, 그룹에서 봇을 <b>관리자</b>로 지정하십시오. "
    "그렇지 않으면 일반 채팅이 집계되지 않습니다."
)
REVOKED = ("⚠️ <b>@{username}</b> 봇의 토큰이 폐기되어 봇이 멈췄습니다.\n"
           "BotFather에서 새 토큰을 받아 <b>🤖 내 봇 → 🔑 토큰 교체</b>로 등록해 주십시오.")


def created(username: str, warn: bool) -> str:
    return (
        f"✅ <b>@{esc(username)}</b> 봇을 만들었습니다!\n\n"
        "<blockquote>1️⃣ 봇을 <b>그룹에 초대</b>하고 <b>관리자</b>로 지정하십시오.\n"
        "2️⃣ 그룹에서 <b>/순위</b> 로 동작을 확인하십시오.\n"
        "3️⃣ 최소 글자·발표 일정·이름·경품은 <b>🤖 내 봇</b>에서 바꾸실 수 있습니다.</blockquote>"
        + (PRIVACY_WARN if warn else "")
    )


def panel(bot, groups: int, warn: bool) -> str:
    prizes = store.prize_list(bot)
    prize_line = " / ".join(f"{i + 1}등 {esc(p)}" for i, p in enumerate(prizes)) if prizes else "없음 (순위만 발표)"
    qualify = f"주간 {bot['qualify']:,}회 이상" if bot["qualify"] else "제한 없음"
    nxt = schedule.stamp(bot["next_settle_at"]) if bot["auto_on"] else "—"
    state = {"active": "", "revoked": "\n🛑 <b>토큰이 폐기되어 멈춘 봇입니다.</b> 토큰을 교체해 주십시오."}.get(bot["status"], "")
    return (
        f"🤖 <b>@{esc(bot['username'])}</b> 설정{state}\n\n"
        "<blockquote>"
        f"🏷 이름 — <b>{esc(bot['name'] or bot['username'])}</b>\n"
        f"✏️ 최소 글자 — <b>{bot['min_chars']}자</b> 이상\n"
        f"📣 자동 발표 — <b>{'ON' if bot['auto_on'] else 'OFF'}</b>\n"
        f"🗓 발표 일정 — <b>{esc(schedule.schedule_text(bot['weekday'], bot['hour'], bot['minute']))}</b>\n"
        f"⏰ 다음 발표 — <b>{nxt}</b>\n"
        f"🎁 경품 — {prize_line}\n"
        f"🎟 응모 자격 — {qualify}\n"
        f"🏠 연결된 그룹 — <b>{groups}곳</b> · 현재 <b>{bot['round']}회차</b>"
        "</blockquote>"
        + (PRIVACY_WARN if warn else "")
    )


# ---- 입력 안내 ----

ASK = {
    "min": "✏️ 집계할 <b>최소 글자 수</b>를 숫자로 보내 주십시오. (1~100, 현재 {cur}자)",
    "time": "⏰ 발표할 <b>시간</b>을 보내 주십시오.\n예) <code>21:00</code>, <code>오후 9시</code>, <code>9시 30분</code> (현재 {cur})",
    "name": "🏷 새 <b>봇 이름</b>을 보내 주십시오. (1~64자)\n※ 텔레그램이 짧은 기간 안의 잦은 변경을 제한합니다.",
    "prizes": "🎁 <b>경품</b>을 1등부터 한 줄에 하나씩 보내 주십시오. (최대 10줄)\n경품 없이 순위만 발표하려면 <code>없음</code> 을 보내십시오.",
    "qualify": "🎟 수상 <b>응모 자격</b>(주간 최소 채팅 횟수)을 숫자로 보내 주십시오.\n제한을 없애려면 <code>0</code> 을 보내십시오. (현재 {cur})",
    "token": "🔑 <b>@{username}</b> 의 새 토큰을 보내 주십시오.",
}

BAD = {
    "min": "숫자(1~100)로 다시 보내 주십시오.",
    "time": "시간 형식을 읽지 못했습니다. <code>21:00</code> 또는 <code>오후 9시</code> 처럼 보내 주십시오.",
    "name": "1~64자로 다시 보내 주십시오.",
    "prizes": "경품은 한 줄에 60자 이내, 최대 10줄입니다. 다시 보내 주십시오.",
    "qualify": "0 이상의 숫자로 다시 보내 주십시오.",
}


# ---- 버튼 ----

def menu_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="➕ 봇 만들기", callback_data="add"), Btn(text="🤖 내 봇", callback_data="my")],
                               [Btn(text="❓ 사용 방법", callback_data="help")]])


def list_kb(bots) -> Kb:
    rows = [[Btn(text=f"🤖 @{b['username']}" + (" 🛑" if b["status"] == "revoked" else ""),
                 callback_data=f"b:{b['bot_id']}")] for b in bots]
    rows.append([Btn(text="➕ 봇 만들기", callback_data="add")])
    return Kb(inline_keyboard=rows)


def panel_kb(bot) -> Kb:
    i = bot["bot_id"]
    return Kb(inline_keyboard=[
        [Btn(text="✏️ 최소 글자", callback_data=f"e:{i}:min"), Btn(text="🏷 봇 이름", callback_data=f"e:{i}:name")],
        [Btn(text=f"📣 자동 발표 {'끄기' if bot['auto_on'] else '켜기'}", callback_data=f"t:{i}")],
        [Btn(text="🗓 발표 요일", callback_data=f"wd:{i}"), Btn(text="⏰ 발표 시간", callback_data=f"e:{i}:time")],
        [Btn(text="🎁 경품", callback_data=f"e:{i}:prizes"), Btn(text="🎟 응모 자격", callback_data=f"e:{i}:qualify")],
        [Btn(text="🔑 토큰 교체", callback_data=f"e:{i}:token"), Btn(text="🗑 삭제", callback_data=f"del:{i}")],
        [Btn(text="⬅️ 목록", callback_data="my")],
    ])


def weekday_kb(bot) -> Kb:
    i = bot["bot_id"]
    row = [Btn(text=("✅ " if n == bot["weekday"] else "") + d, callback_data=f"d:{i}:{n}")
           for n, d in enumerate(schedule.WEEKDAYS)]
    return Kb(inline_keyboard=[row[:4], row[4:], [Btn(text="⬅️ 돌아가기", callback_data=f"b:{i}")]])


def confirm_kb(bot_id: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="🗑 삭제합니다", callback_data=f"delok:{bot_id}"),
                                Btn(text="취소", callback_data=f"b:{bot_id}")]])


def back_kb(bot_id: int) -> Kb:
    return Kb(inline_keyboard=[[Btn(text="⬅️ 돌아가기", callback_data=f"b:{bot_id}")]])
