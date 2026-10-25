"""고객 봇 토큰 암호화 저장. 키(TOKEN_KEY)는 .env에만 둔다."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class TokenBox:
    def __init__(self, key: str):
        try:
            self._f = Fernet(key.strip().encode())
        except (ValueError, TypeError) as e:
            raise RuntimeError(
                "TOKEN_KEY가 올바른 키가 아니에요. 아래 명령으로 만들어 .env에 넣어 주세요.\n"
                'python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from e

    def seal(self, token: str) -> str:
        return self._f.encrypt(token.encode()).decode()

    def open(self, blob: str) -> str:
        try:
            return self._f.decrypt(blob.encode()).decode()
        except InvalidToken as e:
            raise RuntimeError("저장된 토큰을 풀 수 없어요. TOKEN_KEY가 바뀌었는지 확인해 주세요.") from e
