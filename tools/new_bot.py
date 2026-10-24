#!/usr/bin/env python3
"""새 봇 템플릿 생성기: python3 tools/new_bot.py <이름>"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENV_EXAMPLE = """# BotFather에서 받은 토큰
BOT_TOKEN=
# 관리자 텔레그램 ID (쉼표로 여러 명)
ADMIN_IDS=
"""

MAIN = '''from pathlib import Path

from core import run_bot
from bots.{name}.handlers import router
from bots.{name}.texts import TEXTS

if __name__ == "__main__":
    run_bot(Path(__file__).parent, routers=[router], texts=TEXTS)
'''

TEXTS_PY = '''"""이 봇의 말투. core/texts.py와 같은 키를 쓰면 덮어쓴다. 규칙은 docs/STYLE_GUIDE.md."""

TEXTS = {{
    "start": [
        "{{name}}님 왔어요 🙌\\n하고 싶은 말을 보내주면 그대로 돌려줄게요.",
        "어서 와요, {{name}}님.\\n뭐든 보내보세요, 따라 말해 줄게요.",
    ],
    "pong": ["퐁", "퐁 🏓"],
}}
'''

HANDLERS = '''from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.style import esc
from core.texts import Texts

router = Router(name="{name}")


@router.message(Command("ping"))
async def ping(message: Message, texts: Texts) -> None:
    await message.answer(texts.get("pong"))


@router.message()
async def echo(message: Message) -> None:
    if message.text:
        await message.answer(esc(message.text))
'''


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z][a-z0-9_]*", sys.argv[1]):
        print("사용법: python3 tools/new_bot.py <이름>  (영소문자·숫자·밑줄, 영문으로 시작)")
        return 2
    name = sys.argv[1]
    d = ROOT / "bots" / name
    if d.exists():
        print(f"이미 있어요: {d}")
        return 1
    (d / "handlers").mkdir(parents=True)
    (d / "__init__.py").write_text("")
    (d / ".env.example").write_text(ENV_EXAMPLE)
    (d / "main.py").write_text(MAIN.format(name=name))
    (d / "texts.py").write_text(TEXTS_PY.format())
    (d / "handlers" / "__init__.py").write_text(HANDLERS.format(name=name))
    print(f"만들었어요: {d}\n.env.example을 .env로 복사해 토큰을 적고 `pm2 reload ecosystem.config.js` 하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
