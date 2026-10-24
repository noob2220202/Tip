"""aiogram을 붙여 테스트하는 가짜 텔레그램 Bot API 서버 (aiohttp)."""
from __future__ import annotations

import json
from typing import Any

from aiohttp import web

BOT_USERNAME = "tip_test_bot"


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.members: dict[tuple[str, int], str] = {}   # (chat_id, user_id) -> status
        self.forbidden: set[int] = set()                # 이 사용자에게 보내면 403
        self.retry_once: set[int] = set()               # 이 사용자에게 처음 보낼 때 429
        self.sets: dict[str, dict] = {}
        self._msg_id = 100
        self.runner: web.AppRunner | None = None
        self.base = ""

    # ---- 조회 헬퍼 ----
    def of(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def texts_to(self, chat_id: int) -> list[str]:
        out = []
        for m, p in self.calls:
            if m in ("sendMessage", "editMessageText") and str(p.get("chat_id")) == str(chat_id):
                out.append(p.get("text", ""))
        return out

    # ---- 서버 ----
    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/bot{token}/{method}", self._handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = self.runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    def _ok(self, result: Any) -> web.Response:
        return web.json_response({"ok": True, "result": result})

    def _err(self, code: int, desc: str, **params: Any) -> web.Response:
        body: dict[str, Any] = {"ok": False, "error_code": code, "description": desc}
        if params:
            body["parameters"] = params
        return web.json_response(body, status=code)

    def _message(self, chat_id: Any, text: str = "") -> dict:
        self._msg_id += 1
        return {"message_id": self._msg_id, "date": 0, "chat": {"id": int(chat_id), "type": "private"},
                "text": text, "from": {"id": 1, "is_bot": True, "first_name": "bot"}}

    async def _params(self, request: web.Request) -> dict[str, Any]:
        p: dict[str, Any] = {}
        if request.content_type.startswith("multipart"):
            async for part in await request.multipart():
                if part.filename:
                    p.setdefault("_files", {})[part.name] = len(await part.read())
                else:
                    p[part.name] = (await part.read()).decode()
        elif request.can_read_body:
            raw = await request.text()
            if request.content_type == "application/json":
                p = json.loads(raw or "{}")
            else:
                from urllib.parse import parse_qsl
                p = dict(parse_qsl(raw))
        return p

    async def _handle(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        p = await self._params(request)
        self.calls.append((method, p))

        if method == "getMe":
            return self._ok({"id": 1, "is_bot": True, "first_name": "tip", "username": BOT_USERNAME})
        if method in ("deleteWebhook", "answerCallbackQuery", "deleteMessage"):
            return self._ok(True)
        if method == "getChatMember":
            status = self.members.get((str(p["chat_id"]), int(p["user_id"])), "left")
            return self._ok({"status": status, "user": {"id": int(p["user_id"]), "is_bot": False, "first_name": "u"},
                             **({"is_member": True} if status == "restricted" else {})})
        if method in ("sendMessage", "sendPhoto", "sendVideo", "sendDocument"):
            uid = int(p["chat_id"])
            if uid in self.retry_once:
                self.retry_once.discard(uid)
                return self._err(429, "Too Many Requests: retry after 1", retry_after=1)
            if uid in self.forbidden:
                return self._err(403, "Forbidden: bot was blocked by the user")
            return self._ok(self._message(uid, p.get("text", "")))
        if method == "editMessageText":
            return self._ok(self._message(p["chat_id"], p.get("text", "")))
        if method == "getStickerSet":
            s = self.sets.get(p["name"])
            if not s:
                return self._err(400, "Bad Request: STICKERSET_INVALID")
            return self._ok({"name": p["name"], "title": s["title"], "sticker_type": "regular",
                             "stickers": [{"file_id": "f", "file_unique_id": "u", "type": "regular", "width": 512,
                                          "height": 512, "is_animated": False, "is_video": True}
                                         for _ in s["stickers"]]})
        if method == "createNewStickerSet":
            if p["name"] in self.sets:
                return self._err(400, "Bad Request: sticker set name is already occupied")
            self.sets[p["name"]] = {"title": p["title"], "user_id": int(p["user_id"]),
                                    "stickers": json.loads(p["stickers"]), "files": p.get("_files", {})}
            return self._ok(True)
        if method == "addStickerToSet":
            self.sets[p["name"]]["stickers"].append(json.loads(p["sticker"]))
            return self._ok(True)
        if method == "deleteStickerSet":
            self.sets.pop(p["name"], None)
            return self._ok(True)
        return self._ok(True)
