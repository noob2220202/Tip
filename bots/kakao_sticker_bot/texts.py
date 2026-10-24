"""카카오 스티커 봇의 말투. 규칙은 docs/STYLE_GUIDE.md."""
from core.style import b, code, quote

TEXTS = {
    "start": [
        "{name}님 왔어요 🙌\n카카오 이모티콘샵 링크를 붙여넣으면 텔레그램 스티커로 만들어 줄게요.",
        "어서 와요, {name}님.\n이모티콘샵 링크 하나만 보내주세요. 스티커로 옮겨 드릴게요.",
    ],
    "help": [
        "이모티콘샵에서 마음에 드는 이모티콘 링크를 복사해서 여기 붙여넣어요.\n"
        + quote("e.kakao.com/t/…\nemoticon.kakao.com/items/…")
        + "\n움직이는 건 움직이는 그대로, 정지 이미지는 정지 스티커로 만들어져요. 세트 이름은 항상 "
        + b("TIPㅣ이모티콘이름") + " 이에요.",
    ],
    "need_link": [
        "이모티콘샵 링크를 보내주세요. " + code("e.kakao.com/t/…") + " 처럼 생긴 주소예요.",
        "링크를 못 찾았어요. 카카오 이모티콘샵에서 주소를 복사해서 붙여넣어 줘요.",
    ],
    "busy": [
        "아직 만드는 중이에요. 끝나면 알려줄게요.",
        "하나 만드는 중이라 조금만 기다려줘요 🙏",
    ],
    "fetching": ["이모티콘 정보 가져오는 중…", "링크 확인하는 중…"],
    "progress": ["변환하는 중 {done}/{total}"],
    "uploading": ["텔레그램에 올리는 중…"],
    "done": [
        "다 됐어요 ✅ " + b("{title}") + " ({count}개)\n{url}",
        "만들었어요 🎉 " + b("{title}") + " ({count}개)\n{url}",
    ],
    "reuse": [
        "이건 전에 만들어 둔 게 있어요. " + b("{title}") + " ({count}개)\n{url}",
    ],
    "partial": ["\n{skipped}개는 변환이 안 돼서 뺐어요."],
    "mixed": ["\n정지 이미지 {still}개는 형식이 달라서 뺐어요. 한 세트엔 같은 형식만 들어가요."],
    "truncated": ["\n한 세트에는 {max}개까지만 들어가서 앞에서부터 담았어요."],
    "convert_failed": ["변환이 전부 실패했어요. 다른 이모티콘으로 한 번 해봐줘요."],
    "upload_failed": ["올리다가 끊겼어요. 다시 한 번 링크를 보내주면 처음부터 할게요."],
    "no_session": ["지금 움직이는 이모티콘을 받아오는 쪽이 점검 중이에요. 잠시 뒤에 다시 보내줘요."],
    "admin_session_alert": ["카카오 로그인 세션이 없거나 만료됐어요. kakaot에서 npm run login 후 session.json을 다시 복사해 주세요."],
}
