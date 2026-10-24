"""공통 안내문. 봇별 texts.py의 TEXTS로 같은 키를 덮어써서 말투를 바꾼다.

각 키는 문구 후보 리스트이고, 호출할 때마다 무작위로 하나를 고른다.
형식 인자({name} 등)에 들어가는 사용자 입력은 호출하는 쪽에서 esc()로 감싼다.
"""
from __future__ import annotations

import random
from typing import Mapping, Sequence

from core.style import b, code, u

TEXTS: dict[str, list[str]] = {
    "start": [
        "{name}님 왔어요 🙌\n쓰는 법은 /help 한 번 보면 돼요.",
        "어서 와요, {name}님.\n궁금한 건 /help 에 적어뒀어요.",
    ],
    "help": [
        "이 봇은 아직 기능이 없는 기본 상태예요.\n봇별 안내는 각 봇에서 따로 바꿔 써요.",
    ],
    "gate_intro": [
        "채널 {count}곳만 먼저 눌러주세요 👇\n가입하고 " + u("가입했어요") + " 누르면 바로 이어서 할게요.",
        "아래 채널부터 들어와 주세요 👇\n끝나면 " + u("가입했어요") + " 눌러주면 돼요.",
    ],
    "gate_button": ["가입했어요"],
    "gate_ok": ["확인됐어요 👍", "좋아요, 확인했어요."],
    "gate_missing": [
        "아직 안 들어온 채널이 있어요. 다시 한 번만 확인해 볼게요.",
        "{missing} 가입이 아직 안 보여요. 들어간 뒤에 다시 눌러줘요.",
    ],
    "error": [
        "잠깐 꼬였어요. 다시 한 번만 눌러줘요 🙏",
        "어, 뭔가 잘 안 됐어요. 조금 있다가 다시 해줘요.",
    ],
    "admin_only": ["관리자만 쓸 수 있는 명령이에요."],
    "stats": [
        "전체 " + b("{total}") + "명 · 활성 " + b("{active}") + "명\n"
        "오늘 새로 온 사람 " + b("{new_today}") + "명\n"
        "최근 7일 활성 " + b("{active_7d}") + "명",
    ],
    # 브로드캐스트 플로우
    "bc_ask_content": [
        "보낼 광고를 이 채팅에 그대로 보내주세요. 텍스트, 사진, 영상 중 하나예요.\n"
        "굵게·기울임 같은 서식은 보낸 그대로 유지돼요. 취소는 /cancel",
    ],
    "bc_ask_buttons": [
        "버튼은 최대 2개까지 달 수 있어요. 한 줄에 하나씩 " + code("버튼글자 | https://주소") + " 형태로 보내주세요.\n"
        "버튼 없이 가려면 /skip",
    ],
    "bc_bad_content": ["텍스트, 사진, 영상만 받을 수 있어요. 다시 보내주세요."],
    "bc_bad_buttons": ["버튼 형식이 안 맞아요. " + code("글자 | https://주소") + " 로 한 줄에 하나씩, 최대 2개예요."],
    "bc_preview_head": ["이대로 나가요. 대상 " + b("{target}") + "명 (캠페인 #{id})"],
    "bc_cancelled": ["취소했어요."],
    "bc_started": ["발송 시작했어요. 진행률은 이 메시지에 계속 갱신돼요."],
    "bc_progress": ["캠페인 #{id} 발송 중 · {done}/{target} (성공 {sent} · 차단 {blocked} · 실패 {failed})"],
    "bc_done": ["캠페인 #{id} 끝났어요. 성공 " + b("{sent}") + " · 차단 {blocked} · 실패 {failed}"],
    "bc_not_found": ["그런 캠페인은 없어요."],
}


class Texts:
    def __init__(self, overrides: Mapping[str, Sequence[str]] | None = None):
        self._t: dict[str, list[str]] = {k: list(v) for k, v in TEXTS.items()}
        for k, v in (overrides or {}).items():
            self._t[k] = [v] if isinstance(v, str) else list(v)

    def get(self, key: str, **kw: object) -> str:
        return random.choice(self._t[key]).format(**kw)
