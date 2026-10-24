"""공통 설정(common.toml) + 봇별 .env 로드."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Channel:
    chat_id: str | int
    url: str
    title: str = ""


@dataclass(frozen=True)
class CommonConfig:
    channels: tuple[Channel, ...] = ()
    cache_ttl: int = 60
    fail_mode: str = "allow"
    exempt_admins: bool = True
    rate_per_sec: int = 25


@dataclass(frozen=True)
class BotConfig:
    name: str
    bot_dir: Path
    token: str
    admin_ids: frozenset[int]
    db_path: Path
    common: CommonConfig
    env: dict[str, str] = field(default_factory=dict)
    api_base: str | None = None  # 테스트용 가짜 Bot API 서버 주소


def load_common(path: Path | None = None) -> CommonConfig:
    path = path or Path(os.environ.get("COMMON_CONFIG", ROOT / "config" / "common.toml"))
    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    gate = data.get("gate", {})
    channels = tuple(
        Channel(
            chat_id=c["chat_id"],
            url=c["url"],
            title=c.get("title", ""),
        )
        for c in gate.get("channels", [])
    )
    fail_mode = gate.get("fail_mode", "allow")
    if fail_mode not in ("allow", "block"):
        raise ValueError(f"gate.fail_mode는 allow 또는 block이어야 해요: {fail_mode!r}")
    return CommonConfig(
        channels=channels,
        cache_ttl=int(gate.get("cache_ttl", 60)),
        fail_mode=fail_mode,
        exempt_admins=bool(gate.get("exempt_admins", True)),
        rate_per_sec=int(data.get("broadcast", {}).get("rate_per_sec", 25)),
    )


def load_bot_config(bot_dir: Path | str, common: CommonConfig | None = None) -> BotConfig:
    bot_dir = Path(bot_dir).resolve()
    env = {k: v for k, v in dotenv_values(bot_dir / ".env").items() if v is not None}
    token = env.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(f"{bot_dir / '.env'}에 BOT_TOKEN이 없어요.")
    admin_ids = frozenset(
        int(x) for x in env.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
    )
    data_dir = bot_dir / "data"
    data_dir.mkdir(exist_ok=True)
    return BotConfig(
        name=bot_dir.name,
        bot_dir=bot_dir,
        token=token,
        admin_ids=admin_ids,
        db_path=data_dir / "bot.db",
        common=common or load_common(),
        env=env,
        api_base=env.get("TELEGRAM_API_BASE") or None,
    )
