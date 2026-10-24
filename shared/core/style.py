"""서식 헬퍼 (HTML parse mode). 사용자 입력은 항상 esc()로 이스케이프한다."""
from __future__ import annotations

import html
import random
from typing import Sequence, TypeVar

T = TypeVar("T")

PARSE_MODE = "HTML"


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def b(s: object) -> str:
    return f"<b>{esc(s)}</b>"


def i(s: object) -> str:
    return f"<i>{esc(s)}</i>"


def u(s: object) -> str:
    return f"<u>{esc(s)}</u>"


def s(s_: object) -> str:
    return f"<s>{esc(s_)}</s>"


def code(s_: object) -> str:
    return f"<code>{esc(s_)}</code>"


def spoiler(s_: object) -> str:
    return f"<tg-spoiler>{esc(s_)}</tg-spoiler>"


def quote(s_: object, expandable: bool = False) -> str:
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{esc(s_)}</{tag}>"


def link(text: object, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(text)}</a>'


def pick(options: Sequence[T]) -> T:
    return random.choice(options)
