# 텔레그램 멀티봇 프로젝트 명세

하나의 VPS에서 텔레그램 봇 여러 개를 pm2로 운영하는 Python 프로젝트. 봇을 계속 추가할 예정이므로 **새 봇 추가가 쉬운 구조**가 핵심이다.

문체·서식 규칙은 [STYLE_GUIDE.md](STYLE_GUIDE.md)에 따로 있다.

## 변경 이력

- 클릭 추적은 **제외**한다. 추적 URL, 클릭 서버, CTR 지표는 만들지 않는다.
- 봇이 AI로 만든 티가 나지 않도록 문체와 서식 규칙을 적용한다.
- 진행 방식 1번(계획 보여주고 승인 후 구현)은 완료됐다. 구현 승인을 받았고 범위는 core 전체 + 카카오 스티커 봇이다.
- 두 번째 봇으로 `kakao_sticker_bot`(카카오 이모티콘샵 링크 → 텔레그램 스티커 세트)을 추가했다. 세트 제목은 항상 `TIPㅣ이름`이다.

## 제약

- Python 3 + aiogram 3 (async), 롱폴링 방식
- DB는 SQLite 필수 (aiosqlite 또는 표준 sqlite3, WAL 모드). 봇마다 DB 파일 분리
- Docker 사용 금지, 가상환경(venv) 사용 금지. 시스템 Python에 직접 설치한다
  (`pip install --break-system-packages` 또는 apt 패키지 사용, `requirements.txt`로 버전 고정)
- 프로세스 관리는 pm2 (interpreter: python3)
- 서버는 새로 만든 Linux 가상서버, SSH 접속 환경

## 폴더 구조

```
Tip/
├── core/
│   ├── config.py          # 공통 설정 로드 (common.toml + 봇 .env)
│   ├── bot_runner.py      # run_bot(): Bot/Dispatcher/미들웨어/라우터 조립 + 폴링 + 에러 핸들러
│   ├── db.py              # aiosqlite 연결(WAL, busy_timeout), 마이그레이션
│   ├── users.py           # 사용자 등록/조회/비활성 처리/통계
│   ├── subscription.py    # 구독 게이트 미들웨어 + 캐시
│   ├── admin.py           # AdminFilter, /stats, /campaigns
│   ├── style.py           # 서식 헬퍼 (HTML parse mode)
│   ├── texts.py           # 공통 안내문
│   ├── logging_setup.py   # 로깅 + 토큰 마스킹 필터
│   └── broadcast/
│       ├── models.py      # 캠페인/수신자 DB 접근
│       ├── flow.py        # /broadcast FSM (작성 → 미리보기 → 확인)
│       ├── sender.py      # 백그라운드 발송 워커 (레이트리밋, 429, 403, 재개)
│       └── report.py      # /adstats, 텍스트 리포트 내보내기
├── config/
│   ├── common.toml        # 필수 채널, 캐시 TTL, 게이트 fail 모드, 레이트리밋
│   └── common.example.toml
├── bots/
│   ├── example_bot/       # 첫 번째 ping/echo 검증용 봇
│   │   ├── .env.example   # BOT_TOKEN, ADMIN_IDS
│   │   ├── main.py
│   │   ├── texts.py
│   │   ├── handlers/
│   │   └── data/          # bot.db (gitignore)
│   └── kakao_sticker_bot/ # 이모티콘샵 링크 → 텔레그램 스티커 세트
│       ├── kakao.py       # 링크 파싱, 팩 조회, 세션 쿠키 (kakaot/server.js 포팅)
│       ├── media.py       # WebP → WEBM(비디오 스티커) / 정적 스티커 변환 (Pillow + ffmpeg)
│       ├── stickers.py    # 세트 이름·제목 규칙, 세트 생성
│       └── handlers/convert.py
├── tools/new_bot.py       # 봇 템플릿 생성기
├── scripts/
│   ├── setup_server.sh    # Python/pip/pm2/Node 설치, ufw, 스왑, 의존성 설치
│   ├── deploy.sh          # git pull → pip install -r requirements.txt → pm2 reload
│   ├── backup_db.sh       # sqlite3 .backup, 7일 보관
│   └── install_cron.sh    # 일일 백업 cron 등록
├── ecosystem.config.js    # bots/ 폴더를 스캔해 pm2 앱 목록 자동 생성
├── requirements.txt       # 버전 고정
├── docs/
│   ├── PROJECT_SPEC.md
│   └── STYLE_GUIDE.md
├── .gitignore
└── README.md
```

### 새 봇 추가 흐름

1. `python3 tools/new_bot.py <이름>`으로 `bots/<이름>/` 템플릿을 생성한다 (`.env.example`, `main.py`, `handlers`).
2. `.env`에 토큰과 관리자 ID를 적는다.
3. `pm2 reload ecosystem.config.js` (또는 `scripts/deploy.sh`)로 반영한다.

봇 폴더에는 `main.py`와 `handlers/`만 두고, 공통 기능은 `core.run_bot(bot_dir, routers=[...])` 한 줄로 붙인다.

## core 기능

### 구독 게이트 미들웨어

- 공통 필수 채널 목록(`config/common.toml` 한 곳)에 전부 가입해야 봇을 쓸 수 있다.
- `getChatMember`로 확인한다. 미가입이면 가입 링크 인라인 버튼과 "가입 확인" 버튼을 보여주고, 확인을 통과하면 원래 요청을 이어서 진행한다.
- 멤버십 결과는 짧게 캐시한다(기본 60초, 설정 가능).
- API 오류 시 동작(허용/차단)은 설정으로 고른다.
- 메시지, 콜백, 인라인 등 모든 update 유형에 적용한다. 단, `/start`는 안내 후 사용자를 등록한다.
- 필수 채널 설정은 `chat_id`(`@username` 또는 숫자)와 가입 링크를 함께 적는다. 비공개 채널은 초대 링크를 직접 적는다.
- 관리자는 게이트를 면제하는 것이 기본값이고, 설정으로 바꿀 수 있다.

### 사용자 DB

- `/start`를 보낸 사용자만 등록한다.
- 발송 중 403(차단)이 오면 `is_active=0`으로 바꾼다. `my_chat_member` 업데이트로도 차단/해제를 감지한다.

### 관리자 기능

- 관리자는 `.env`의 `ADMIN_IDS`로만 허용한다.
- `/stats`: 전체 사용자 수, 활성 사용자 수, 오늘 신규, 최근 7일 활성
- `/broadcast`, `/adstats <캠페인ID>`, `/campaigns`(목록·상태)

### 로깅·에러 핸들링

- 토큰 패턴(`\d+:[A-Za-z0-9_-]{30,}`)을 로그에서 마스킹한다. aiogram 요청 URL 로그는 끈다.
- 시크릿은 `.env`로만 관리하고 `.gitignore` 처리한다.

## 광고 브로드캐스트

광고는 자동 노출이 아니라 **관리자가 수동으로** 보낸다. 봇별로 따로 발송하고, 그 봇에 `/start` 이력이 있는 활성 사용자 전체가 대상이다.

### 플로우

1. 관리자 전용 `/broadcast`
2. 광고 메시지 작성: 텍스트/사진/영상 + 선택적 버튼 1~2개 (원본 URL 그대로 사용)
3. 대상 수를 표시한 미리보기
4. "발송" 확인 버튼
5. 백그라운드 발송

스팸 신고를 막기 위해 확인 단계를 유지하고, 한 사용자에게 같은 캠페인을 중복 발송하지 않는다.

### 발송 규칙

- 초당 약 25건 이하로 제한한다.
- 429가 오면 `retry_after`만큼 기다린 뒤 재시도한다.
- 403은 차단 처리한다. 그 외 오류는 실패로 기록한다.
- 발송 중 진행률을 표시한다.
- 봇이 재시작되어도 이어서 발송한다. 캠페인별 사용자 발송 상태를 DB에 기록한다.

### 광고주 보고용 지표

텔레그램은 노출/열람을 알려주지 않으므로 측정 가능한 것만 쓴다.

- 발송 대상 수 / 전달 성공 수 / 차단·실패 수 / 전달률(성공 ÷ 대상)
- 캠페인별 리포트: `/adstats <캠페인ID>`
- 광고주에게 전달할 수 있도록 캠페인 리포트를 텍스트로 내보내는 기능
- README에 "노출은 측정 불가하고, 대체 지표는 전달 성공 수"라고 명시한다. 클릭도 측정하지 않으며, 필요하면 광고주가 자기 쪽 UTM 링크로 따로 집계한다는 점도 적는다.

## DB 스키마 (봇별 `bots/<name>/data/bot.db`)

모든 DB는 `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000; foreign_keys=ON`을 쓴다.

```sql
CREATE TABLE users (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT,
  started_at  INTEGER NOT NULL,        -- unix ts
  last_seen   INTEGER NOT NULL,
  is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE campaigns (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT NOT NULL,               -- 관리자용 이름
  content_type  TEXT NOT NULL,               -- text | photo | video
  text          TEXT,                        -- 본문/캡션 (HTML)
  file_id       TEXT,                        -- 사진/영상 file_id
  buttons_json  TEXT,                        -- [{"text","url"}] 최대 2개
  status        TEXT NOT NULL,               -- draft | sending | paused | done | cancelled
  target_count  INTEGER NOT NULL DEFAULT 0,  -- 확정 시점 스냅샷
  created_by    INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  started_at    INTEGER,
  finished_at   INTEGER
);

CREATE TABLE campaign_recipients (
  campaign_id  INTEGER NOT NULL REFERENCES campaigns(id),
  user_id      INTEGER NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | blocked | failed
  error        TEXT,
  sent_at      INTEGER,
  PRIMARY KEY (campaign_id, user_id)             -- 같은 캠페인 중복 발송 방지
);
CREATE INDEX idx_recip_pending ON campaign_recipients(campaign_id, status);
```

멤버십 캐시는 메모리에만 둔다.

## 운영

- **pm2**: 봇별 앱 등록, 자동 재시작, 메모리 초과 시 재시작(`max_memory_restart`), pm2-logrotate, `pm2 startup` + `pm2 save`. `ecosystem.config.js`가 `bots/*/main.py`를 스캔해 앱을 만든다. 새 봇 폴더만 만들면 `pm2 reload`로 반영된다.
- **scripts/setup_server.sh**: Python/pip/pm2/Node 설치, ufw 방화벽, 스왑, 의존성 설치
- **scripts/deploy.sh**: git pull → `pip install -r requirements.txt` → pm2 reload
- **SQLite 백업**: 매일 cron으로 각 봇 DB를 `sqlite3 .backup`으로 복사하고 7일치를 보관한다.
- **필수 채널 관리자 등록**: 봇이 필수 채널의 관리자로 추가되어야 `getChatMember`가 동작한다. README에 명시한다.

## 진행 방식

1. 폴더 구조, 주요 테이블 스키마, 구현 계획을 보여주고 승인 후 구현한다. (계획 제시 완료, 구현 승인 대기)
2. 첫 번째로 ping/echo 수준의 템플릿 봇을 만들어 게이트, 사용자 등록, 브로드캐스트가 실제로 동작하는지 검증한다. 실제 텔레그램은 쓸 수 없으므로 로컬 가짜 Bot API 서버(aiohttp 목)에 aiogram을 연결해 자동 테스트한다.
   - 게이트 차단/통과
   - 사용자 등록
   - 브로드캐스트 발송과 429/403 처리
   - 재시작 후 이어보내기
   - `new_bot.py`가 만든 봇 구동과 `ecosystem.config.js` 자동 스캔
3. 완료 후 README에 서버 배포 절차, 새 봇 추가 방법, 광고 발송 방법을 정리한다.

### 구현 순서

1. 골격: `requirements.txt`, `.gitignore`, `core/config`, `db`, `logging`, `users`
2. 구독 게이트 + `run_bot` + 관리자 `/stats`
3. 브로드캐스트(플로우, 발송 워커, 재개) + 리포트
4. `example_bot` + `tools/new_bot.py` + `ecosystem.config.js`
5. 운영 스크립트(`setup_server.sh`, `deploy.sh`, `backup_db.sh`, cron)
6. 자동 테스트로 검증
7. README 작성

## 의존성

`aiogram`, `aiosqlite`, `aiohttp`(카카오 조회 + 테스트용 목 서버), `python-dotenv`, `Pillow`(스티커 봇의 WebP 프레임 디코딩)를 `requirements.txt`에 버전 고정한다. 테스트 도구(`pytest`, `pytest-asyncio`)는 `requirements-dev.txt`에 둔다. 스티커 봇은 시스템 패키지 `ffmpeg`(libvpx-vp9)도 필요하다. Python 3.11+ 기준으로 `tomllib`를 쓴다.
