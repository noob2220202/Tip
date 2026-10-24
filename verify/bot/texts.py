"""검수 봇의 문구. 공적인 말투(합쇼체)를 쓴다."""

TEXTS = {
    "start": [
        "🛡 <b>{name}님, 안녕하십니까.</b>\n\n"
        "본 봇은 <b>채널 가입 검수 전용</b>입니다. 다른 봇에서 안내하는 대로 이용해 주십시오. 🙏",
    ],
    "help": [
        "🛡 <b>채널 가입 검수 봇</b>\n\n"
        "필수 채널의 가입 여부를 확인하여 다른 봇들이 이용 자격을 판단하도록 돕습니다.\n\n"
        "<b>관리자 명령</b>\n"
        "<blockquote>"
        "<code>/channels</code> — 필수 채널 설정과 봇의 관리자 권한을 점검합니다.\n"
        "<code>/check 사용자ID</code> — 해당 사용자의 채널별 가입 여부를 확인합니다."
        "</blockquote>",
    ],
    "channels_ok": ["✅ <b>정상입니다.</b> 모든 필수 채널에서 봇이 관리자로 확인되었습니다.\n<blockquote>{channels}</blockquote>"],
    "channels_none": ["ℹ️ 설정된 필수 채널이 없습니다. <code>shared/config/common.toml</code>을 확인해 주십시오."],
    "channels_problem": ["🚨 <b>점검에서 문제가 발견되었습니다.</b>\n<blockquote>{problems}</blockquote>"],
    "check_usage": ["사용법: <code>/check 사용자ID</code>"],
    "check_result": ["🔎 <b>사용자 {user_id}</b>\n<blockquote>{lines}</blockquote>"],
    "error": ["⚠️ <b>일시적인 오류가 발생했습니다.</b> 잠시 후 다시 시도해 주십시오."],
    "gate_selfcheck_failed": [
        "🚨 <b>필수 채널 점검에서 문제가 발견되었습니다.</b>\n\n"
        "<blockquote>{problems}</blockquote>\n"
        "봇을 채널의 <u>관리자</u>로 추가한 뒤 <code>pm2 restart verify</code>를 실행해 주십시오.",
    ],
}
