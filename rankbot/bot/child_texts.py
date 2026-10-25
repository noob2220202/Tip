"""고객 봇(그룹에서 채팅을 세는 봇)의 문구와 버튼. 합쇼체에 이모지·굵게·인용을 쓴다. (해요체·절제 규칙의 이 봇 한정 예외)

이름·그룹 제목처럼 사용자가 정한 값은 모두 esc()로 감싼다.
"""
from __future__ import annotations

from html import escape as esc

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import schedule, store

MEDALS = ["🥇", "🥈", "🥉"]
NUMS = ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def icon(rank: int) -> str:
    if rank <= 3:
        return MEDALS[rank - 1]
    return NUMS[rank - 4] if rank - 4 < len(NUMS) else "▫️"


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name)}</a>'


def num(n: int) -> str:
    return f"{n:,}"


def when(bot) -> str:
    if not bot["auto_on"]:
        return "자동 발표 꺼짐"
    return schedule.schedule_text(bot["weekday"], bot["hour"], bot["minute"])


# ---- 버튼 ----

def kb_rank() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 새로고침", callback_data="rank"),
         InlineKeyboardButton(text="🙋 내 기록", callback_data="my")],
        [InlineKeyboardButton(text="📖 규칙", callback_data="rule"),
         InlineKeyboardButton(text="⌨️ 명령어", callback_data="help")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 순위 보기", callback_data="rank"),
         InlineKeyboardButton(text="🙋 내 기록", callback_data="my")],
    ])


# ---- 안내 ----

def prize_block(bot) -> str:
    prizes = store.prize_list(bot)
    if not prizes:
        return ""
    lines = "\n".join(f"{icon(i + 1) if i < 3 else '🎁'} <b>{i + 1}등</b> — <b>{esc(p)}</b>" for i, p in enumerate(prizes))
    cond = (f"\n🎟 주간 <b>{num(bot['qualify'])}회 이상</b> 채팅한 분만 수상할 수 있습니다." if bot["qualify"] else "")
    return f"\n\n🎁 <b>경품</b>\n{lines}{cond}"


def rule_text(bot) -> str:
    return (
        "📖 <b>집계 규칙</b>\n\n"
        f"<blockquote>💬 <b>{bot['min_chars']}글자 이상</b> 채팅 1건 = <b>1카운트</b> (사진 설명 포함)\n"
        "🚫 명령어, 봇 메시지, 입퇴장 알림은 집계하지 않습니다.\n"
        "📊 순위는 채팅 <b>횟수</b>가 많은 순이며, 횟수가 같으면 <b>먼저 달성한 분</b>이 앞섭니다.\n"
        f"🗓 <b>{esc(when(bot))}</b> 순위를 발표하고 카운트를 <b>0으로 초기화</b>합니다.</blockquote>"
        f"{prize_block(bot)}"
    )


def start_text(bot, manager: str) -> str:
    return (
        f"👋 <b>{esc(bot['name'] or bot['username'])}</b>\n"
        "그룹 채팅 횟수를 세어 주간 순위를 발표하는 봇입니다.\n\n"
        "<blockquote>이 봇을 <b>그룹에 초대</b>하고 <b>관리자</b>로 지정하면 바로 집계를 시작합니다.\n"
        "순위는 그룹에서 <b>/순위</b>, 내 기록은 <b>/내기록</b> 으로 확인하십시오.</blockquote>"
        + (f"\n\n🛠 봇 설정은 @{esc(manager)} 에서 하실 수 있습니다." if manager else "")
    )


def help_text(admin: bool) -> str:
    extra = ("\n\n🛠 <b>방장 전용</b>\n<blockquote>🧮 <b>/집계</b> — 지금까지의 순위 미리 발표 (초기화 없음)\n"
             "📊 <b>/상태</b> — 참여 현황 · 남은 시간</blockquote>") if admin else ""
    return (
        "⌨️ <b>명령어</b>\n\n"
        "<blockquote>🙋 <b>/내기록</b> — 내 채팅 횟수 · 순위\n"
        "🏆 <b>/순위</b> — 이번 회차 TOP 10\n"
        "📖 <b>/규칙</b> — 집계 규칙\n"
        "❓ <b>/명령어</b> — 이 목록</blockquote>"
        f"{extra}\n\n"
        "💡 영문 <b>/rank</b> <b>/me</b> <b>/rules</b> 도 같은 기능입니다."
    )


def group_only() -> str:
    return "👉 이 명령어는 <b>그룹 채팅방</b>에서 사용하실 수 있습니다."


# ---- 순위 / 내 기록 ----

def rank_text(bot, rows, me_line: str) -> str:
    if not rows:
        return ("🏆 <b>이번 회차 순위</b>\n\n<blockquote>아직 집계된 채팅이 없습니다. 첫 번째 주인공이 되어 보십시오! 💬</blockquote>")
    lines = "\n".join(f"{icon(i + 1)} {mention(r['user_id'], r['name'])} — <b>{num(r['messages'])}회</b>"
                      for i, r in enumerate(rows))
    return f"🏆 <b>이번 회차 TOP {len(rows)}</b>\n\n<blockquote>{lines}</blockquote>\n\n{me_line}"


def me_line(messages: int, rank: int, people: int) -> str:
    if not messages:
        return "🙋 아직 집계된 채팅이 없습니다. 지금부터 시작해 보십시오!"
    return f"🙋 내 기록 — <b>{rank}위</b> / {people}명 · <b>{num(messages)}회</b>"


def my_text(bot, name: str, messages: int, rank: int, people: int) -> str:
    qualify = ""
    if bot["qualify"] and store.prize_list(bot):
        left = max(0, bot["qualify"] - messages)
        qualify = (f"\n🎟 응모 자격 — {'<b>달성</b> ✅' if not left else f'<b>{num(left)}회</b> 남음'}"
                   f" ({num(bot['qualify'])}회 이상)")
    if not messages:
        body = "아직 집계된 채팅이 없습니다. 💬"
    else:
        body = f"🏅 순위 — <b>{rank}위</b> / {people}명\n💬 채팅 — <b>{num(messages)}회</b>"
    return f"🙋 <b>{esc(name)}</b>님의 기록\n\n<blockquote>{body}{qualify}</blockquote>"


def my_alert(name: str, messages: int, rank: int, people: int) -> str:
    if not messages:
        return f"{name}님은 아직 집계된 채팅이 없습니다."
    return f"{name}님\n{rank}위 / {people}명\n채팅 {num(messages)}회"


def status_text(bot, title: str, people: int, total: int, qualified: int, left: float) -> str:
    q = f"\n🎟 응모 자격 {qualified}명 ({num(bot['qualify'])}회 이상)" if bot["qualify"] and store.prize_list(bot) else ""
    deadline = (f"⏳ 발표까지 {schedule.remaining(left)}" if bot["auto_on"] else "⏸ 자동 발표 꺼짐")
    return (
        "🛠 <b>봇 상태</b>\n\n"
        f"<blockquote>🏠 {esc(title)}\n"
        f"🗓 {bot['round']}회차 · {esc(when(bot))}\n"
        f"👥 참여 {people}명 · 💬 총 {num(total)}회{q}\n{deadline}</blockquote>"
    )


# ---- 발표 ----

def result_text(bot, round_: int, rows, winners: list[dict], preview: bool = False) -> str:
    head = "🧮 <b>중간 집계</b> (미리보기)" if preview else f"🏆 <b>{round_}회차 채팅 순위 발표</b>"
    top = rows[:10]
    lines = "\n".join(f"{icon(i + 1)} {mention(r['user_id'], r['name'])} — <b>{num(r['messages'])}회</b>"
                      for i, r in enumerate(top))
    out = f"{head}\n\n<blockquote>{lines}</blockquote>"
    if winners:
        wl = "\n".join(f"{icon(w['rank'])} {mention(w['user_id'], w['name'])} — <b>{esc(w['prize'])}</b>"
                       for w in winners)
        out += f"\n\n🎁 <b>수상자</b>\n<blockquote>{wl}</blockquote>"
    elif store.prize_list(bot) and not preview:
        out += "\n\n🎁 이번 회차는 응모 자격을 채운 분이 없어 수상자가 없습니다."
    out += f"\n\n👥 참여 {len(rows)}명"
    if preview:
        out += "\n※ 미리보기라 카운트는 초기화되지 않았습니다."
    else:
        out += f"\n♻️ 카운트를 초기화했습니다. 다음 발표는 <b>{esc(when(bot))}</b>입니다."
    return out
