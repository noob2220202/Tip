"""기프트콘 이벤트 봇의 문구와 버튼.

kakao·boost와 같이 합쇼체로 쓰고, 이모지·굵게·인용·코드를 적극적으로 쓴다.
(docs/STYLE_GUIDE.md의 해요체·절제 규칙에 대한 이 봇 한정 예외)
제목·안내문·이름처럼 관리자나 사용자가 정한 값은 모두 esc()로 감싼다.
"""
from __future__ import annotations

import time

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup as Kb

from core.style import esc

KIND = {"draw": "추첨", "all": "전원 동일"}
STATUS = {"draft": "📝 준비 중", "running": "🚀 진행 중", "paused": "⏸ 일시 중지", "done": "✅ 완료", "cancelled": "🗑 취소"}
NOTE_MAX = 300
TITLE_MAX = 60

TEXTS: dict[str, list[str]] = {
    "start": [
        "🎁 <b>기프트콘 이벤트 봇</b>에 오신 것을 환영합니다.\n\n"
        "<blockquote>채널을 <b>구독 중인 분</b>은 별도 신청 없이 <b>이벤트 대상</b>입니다.\n"
        "이벤트가 열리면 <b>추첨으로 당첨</b>되거나 <b>전원에게 기프트콘이 발송</b>됩니다.\n"
        "받으신 기프트콘은 이 대화에서 확인하실 수 있습니다.</blockquote>\n\n"
        "🔔 봇을 <b>차단하지 마십시오</b>. 차단하시면 기프트콘을 받을 수 없습니다.",
    ],
    "help": [
        "❓ <b>안내</b>\n\n"
        "<blockquote>• 필수 채널을 <b>구독 중</b>이고 이 봇을 <b>차단하지 않은</b> 분이 대상입니다.\n"
        "• 이벤트가 진행될 때 구독 여부를 <b>다시 확인</b>하므로, 채널을 나가시면 제외됩니다.\n"
        "• 당첨되시면 이 대화로 기프트콘이 도착합니다.</blockquote>",
    ],
}

NOT_ADMIN = "관리자만 사용할 수 있습니다."
CANCELLED = "취소했습니다."
UNKNOWN = "메뉴에서 선택해 주십시오. 👇"
NOT_FOUND = "해당 이벤트를 찾을 수 없습니다."


def when(ts: int | None) -> str:
    return time.strftime("%m/%d %H:%M", time.gmtime(ts + 9 * 3600)) if ts else "-"     # 한국시간(UTC+9 고정)


# ---- 버튼 ----

def admin_menu_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="➕ 새 이벤트", callback_data="ga:new"), Btn(text="📋 이벤트 목록", callback_data="ga:list")]])


def kind_kb() -> Kb:
    return Kb(inline_keyboard=[
        [Btn(text="🎲 추첨 (N명에게 각자 다른 기프트콘)", callback_data="gk:draw")],
        [Btn(text="🎁 전원 동일 (선착순 사용)", callback_data="gk:all")],
        [Btn(text="⬅️ 메뉴", callback_data="ga:menu")],
    ])


def items_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="✅ 등록 완료", callback_data="gi:done"), Btn(text="취소", callback_data="gi:cancel")]])


def note_kb() -> Kb:
    return Kb(inline_keyboard=[[Btn(text="안내 문구 없이 진행", callback_data="gn:skip")]])


def event_kb(ev) -> Kb:
    i, st = ev["id"], ev["status"]
    rows = []
    if st in ("draft", "paused"):
        rows.append([Btn(text="▶️ 지금 실행" if st == "draft" else "▶️ 이어서 실행", callback_data=f"gx:run:{i}")])
    if st == "draft":
        rows.append([Btn(text="👀 나에게 미리보기", callback_data=f"gx:view:{i}")])
    if st in ("draft", "paused"):
        rows.append([Btn(text="🗑 이벤트 취소", callback_data=f"gx:cancel:{i}")])
    if st in ("done", "cancelled"):
        rows.append([Btn(text="🧹 코드·이미지 지우기", callback_data=f"gx:wipe:{i}")])
    rows.append([Btn(text="🔄 새로고침", callback_data=f"ge:{i}"), Btn(text="📋 목록", callback_data="ga:list")])
    return Kb(inline_keyboard=rows)


def list_kb(events) -> Kb:
    rows = [[Btn(text=f"#{e['id']} {e['title'][:20]} · {STATUS[e['status']]}", callback_data=f"ge:{e['id']}")] for e in events]
    rows.append([Btn(text="⬅️ 메뉴", callback_data="ga:menu")])
    return Kb(inline_keyboard=rows)


# ---- 만들기 ----

ASK_TITLE = f"📝 이벤트 <b>제목</b>을 보내 주십시오. (최대 {TITLE_MAX}자)\n취소는 /cancel"
ASK_COUNT = "🎲 <b>당첨 인원</b>을 숫자로 보내 주십시오. (1~500)"
BAD_COUNT = "1~500 사이의 숫자로 다시 보내 주십시오."
BAD_TITLE = f"1~{TITLE_MAX}자로 다시 보내 주십시오."
BAD_NOTE = f"안내 문구는 {NOTE_MAX}자 이내로 다시 보내 주십시오."
ASK_NOTE = (f"💬 기프트콘과 함께 보낼 <b>안내 문구</b>를 보내 주십시오. (최대 {NOTE_MAX}자)\n"
            "예) 사용 기한, 사용처, 유의사항")


def ask_items(kind: str, winners: int) -> str:
    if kind == "draw":
        return (f"🎁 <b>기프트콘</b>을 등록해 주십시오. 당첨 인원 <b>{winners}명</b> 이상 필요합니다.\n\n"
                "<blockquote>• <b>이미지</b>는 한 장씩 보내십시오.\n"
                "• <b>텍스트 코드</b>는 한 줄에 하나씩 여러 개를 한 번에 보내셔도 됩니다.</blockquote>\n"
                "다 올리셨으면 <b>등록 완료</b>를 누르십시오.")
    return ("🎁 모두에게 보낼 <b>기프트콘 1개</b>를 보내 주십시오.\n\n"
            "<blockquote>이미지 한 장 또는 텍스트 코드를 보내십시오. 텍스트는 보내신 그대로 한 개로 등록합니다.</blockquote>")


def items_progress(kind: str, n: int, winners: int) -> str:
    if kind == "draw":
        return f"✅ 등록 <b>{n}개</b> / 필요 {winners}개" + (" — 더 올리셔도 됩니다(예비)." if n >= winners else "")
    return "✅ 기프트콘을 등록했습니다."


def items_short(n: int, winners: int) -> str:
    return f"⚠️ 기프트콘이 <b>{n}개</b>뿐입니다. 당첨 인원 {winners}명에 맞게 <b>{winners - n}개 더</b> 등록해 주십시오."


def event_text(ev, items: dict, pool: int | None = None, tries: dict | None = None) -> str:
    rows = [f"🎯 방식 — <b>{KIND[ev['kind']]}</b>"]
    if ev["kind"] == "draw":
        rows.append(f"🏆 당첨 인원 — <b>{ev['winners']}명</b>")
    rows.append(f"🎁 기프트콘 — 총 <b>{items['total']}개</b> (남음 {items['free']} · 발송 {items['sent']}"
                + (f" · <b>확인 필요 {items['uncertain']}</b>" if items["uncertain"] else "") + ")")
    if ev["note"]:
        rows.append(f"💬 안내 문구 — {esc(ev['note'])}")
    if pool is not None:
        rows.append(f"👥 대상 후보 — <b>{pool}명</b> (구독 여부는 실행할 때 한 명씩 확인)")
    if tries:
        rows.append(f"📊 당첨·발송 <b>{tries['won']}</b> · 구독 안 함 {tries['unsub']} · 차단 {tries['blocked']}"
                    + (f" · <b>확인 필요 {tries['uncertain']}</b>" if tries["uncertain"] else ""))
    rows.append(f"🕒 생성 {when(ev['created_at'])}" + (f" · 시작 {when(ev['started_at'])}" if ev["started_at"] else "")
                + (f" · 종료 {when(ev['finished_at'])}" if ev["finished_at"] else ""))
    if ev["stop_reason"]:
        rows.append(f"ℹ️ {esc(ev['stop_reason'])}")
    return f"🎁 <b>#{ev['id']} {esc(ev['title'])}</b> — {STATUS[ev['status']]}\n\n<blockquote>" + "\n".join(rows) + "</blockquote>"


def events_text(events) -> str:
    if not events:
        return "📋 만든 이벤트가 없습니다."
    return "📋 <b>이벤트 목록</b>\n\n" + "\n".join(
        f"#{e['id']} {esc(e['title'])} · {KIND[e['kind']]} · {STATUS[e['status']]} · {when(e['created_at'])}" for e in events)


def winners_text(rows) -> str:
    if not rows:
        return ""
    lines = "\n".join(f"{i}. " + (f"@{esc(r['username'])}" if r["username"] else f'<a href="tg://user?id={r["user_id"]}">{r["user_id"]}</a>')
                      + f" (<code>{r['user_id']}</code>)" for i, r in enumerate(rows[:100], 1))
    more = f"\n… 외 {len(rows) - 100}명" if len(rows) > 100 else ""
    return f"\n\n🏆 <b>받은 분</b>\n<blockquote expandable>{lines}{more}</blockquote>"


# ---- 사용자에게 보내는 기프트콘 ----

def delivery(ev, item_kind: str, content: str) -> str:
    """기프트콘과 함께 가는 메시지(이미지는 캡션, 코드는 본문)."""
    head = ("🎉 <b>축하합니다! 당첨되셨습니다.</b>\n" if ev["kind"] == "draw" else "🎁 <b>구독해 주셔서 감사합니다.</b>\n")
    body = f"<blockquote>{esc(ev['title'])}</blockquote>"
    note = f"\n💬 {esc(ev['note'])}" if ev["note"] else ""
    race = "\n⚡ <b>선착순</b>입니다. 먼저 사용하신 분이 사용권을 가집니다." if ev["kind"] == "all" else ""
    code = f"\n\n🎟 <code>{esc(content)}</code>" if item_kind == "text" else ""
    return f"{head}{body}{note}{race}{code}"


# ---- 실행 보고 ----

def progress_text(ev, tries: dict, target: str) -> str:
    return (f"🚀 <b>#{ev['id']} {esc(ev['title'])}</b> 진행 중\n\n"
            f"<blockquote>발송 <b>{tries['won']}</b>{target} · 구독 안 함 {tries['unsub']} · 차단 {tries['blocked']}"
            + (f" · 확인 필요 {tries['uncertain']}" if tries["uncertain"] else "") + "</blockquote>")


def paused_text(ev, reason: str) -> str:
    return (f"⏸ <b>#{ev['id']} {esc(ev['title'])}</b> 이벤트를 일시 중지했습니다.\n\n<blockquote>{esc(reason)}</blockquote>\n"
            "원인을 해결한 뒤 이벤트 목록에서 <b>이어서 실행</b>을 눌러 주십시오. 이미 보낸 분에게는 다시 보내지 않습니다.")
