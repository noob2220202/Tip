# 텔레그램 봇 프로젝트 명세

전용 VPS에서 텔레그램 봇을 pm2로 운영하는 Python 프로젝트. 저장소 하나(`Tip`)에 **통합 폴더 `shared/`** 와 **봇 폴더(`kakao/` 등)** 를 둔다. 공통 기능(채널 가입 검증, 사용자 DB, 관리자, 브로드캐스트, 필수 채널 설정)은 `shared/`에 한 번만 두고 모든 봇이 불러 쓴다. 서버에서는 봇 폴더를 `/root/kakao`처럼 `/root` 아래 링크로 연결해 거기서 돌린다.

문체·서식 규칙은 [STYLE_GUIDE.md](STYLE_GUIDE.md)에 따로 있다.

## 변경 이력

- 클릭 추적은 **제외**한다. 추적 URL, 클릭 서버, CTR 지표는 만들지 않는다.
- 봇이 AI로 만든 티가 나지 않도록 문체와 서식 규칙을 적용한다.
- 진행 방식 1번(계획 보여주고 승인 후 구현)은 완료됐다. 구현 승인을 받았고 범위는 core 전체 + 카카오 스티커 봇이다.
- 첫 봇은 `kakao`(카카오 이모티콘샵 링크 → 텔레그램 스티커 세트)다. 세트 제목은 항상 `@봇이름ㅣ이름`이다(봇 username을 실행 중에 읽어 붙인다).
- 두 번째 봇은 `boost`(그룹·채널 부스트 판매)다. USDT(TRC20)로 결제하고, TronGrid로 입금을 확인하면 SMM 패널(표준 API v2)에 자동으로 주문한다. 입금 인정 조건은 보내는 주소·금액 정확 일치·주문 시간 안의 블록·미사용 txid다. 자세한 내용은 `boost/README.md`.
- 세 번째 봇은 `rankbot`(채팅 순위 봇을 사용자가 직접 만드는 서비스)이다. 관리 봇 하나가 토큰을 받으면 같은 프로세스에서 고객 봇이 폴링을 시작한다. 토큰은 Fernet으로 암호화해 저장하고(`TOKEN_KEY`), 봇 단위로 최소 글자·발표 요일/시간·온오프·이름·경품·응모 자격을 바꾼다. 자세한 내용은 `rankbot/README.md`.
- 네 번째 봇은 `walletbot`(TRON 지갑 입금 알림·잔액·빗썸 시세/김프)이다. 사용자당 지갑 3개. 지갑마다 조회하지 않고 새 블록을 3초마다 받아 직접 걸러서(블록 스캔) 입금을 블록에 들어간 직후 알리고, 확정되면 같은 메시지를 고친다. TronGrid 호출 수가 지갑 수와 무관하다. 블록에 드러나지 않는 입금은 주기적 잔액 대조로 보충한다. 김프는 빗썸 USDT ÷ 무료 환율 API(USD/KRW)로 계산한다. 자세한 내용은 `walletbot/README.md`.
- TRON 주소 검증(`core/tron.py`)과 시각 입력 해석(`core/clock.py`)은 여러 봇이 같이 쓰도록 `shared/core/`로 옮겼다.
- `run_bot()`에 `on_shutdown` 훅을 추가했다(종료 때 DB를 닫기 전에 호출). 백그라운드 작업을 가진 봇이 정리하는 데 쓴다.
- 봇마다 core를 복사하던 방식을 없애고 `shared/` 통합 폴더로 합쳤다. `bots/` 구조와 `tools/new_bot.py`는 없다. 새 봇은 `kakao/`를 참고해 `Tip/<봇>/` 폴더로 만든다.
- 채널 가입 확인은 **검수 봇(`verify`)** 이 맡는다. 채널 관리자는 검수 봇 하나이고, 다른 봇은 같은 서버의 `127.0.0.1` 검수 서비스에 가입 여부를 물어본다(`gate.verifier_url`). 서비스는 `VERIFY_SECRET` 헤더로 인증한다.
- 필수 채널 가입 확인이 실패하면 기본값을 차단(`block`)으로 둔다. 봇은 시작할 때 채널 관리자 여부를 점검해 관리자에게 알린다.
- 이미 만든 스티커 세트는 카카오에 접속하지 않고 바로 돌려준다(사용자 간 공유, 이미지 목록 해시로 링크 형태 차이 흡수).

## 제약

- Python 3 + aiogram 3 (async), 롱폴링 방식
- DB는 SQLite 필수 (aiosqlite 또는 표준 sqlite3, WAL 모드). 봇마다 DB 파일 분리
- Docker 사용 금지, 가상환경(venv) 사용 금지. 시스템 Python에 직접 설치한다
  (`pip install --break-system-packages` 또는 apt 패키지 사용, `requirements.txt`로 버전 고정)
- 프로세스 관리는 pm2 (interpreter: python3)
- 서버는 새로 만든 Linux 가상서버, SSH 접속 환경

## 폴더 구조

```
Tip/                            # 저장소 (서버: /root/Tip)
├── shared/                     # 통합 폴더 — 모든 봇이 같이 쓴다
│   ├── core/
│   │   ├── config.py           # 공통 설정 로드 (common.toml + 봇 .env)
│   │   ├── bot_runner.py       # run_bot(): Bot/Dispatcher/미들웨어/라우터 조립 + 폴링 + 에러 핸들러 + 시작 점검
│   │   ├── db.py               # aiosqlite 연결(WAL, busy_timeout), 마이그레이션
│   │   ├── users.py            # 사용자 등록/조회/비활성 처리/통계
│   │   ├── subscription.py     # 구독 게이트 미들웨어 + 캐시 + 시작 점검(self_check)
│   │   ├── verifier.py         # 검수 서비스: 서버(검수 봇) + 클라이언트(다른 봇의 게이트)
│   │   ├── admin.py            # AdminFilter, /stats, /campaigns
│   │   ├── style.py            # 서식 헬퍼 (HTML parse mode)
│   │   ├── tron.py             # TRON 주소 검증(base58check)·hex 변환
│   │   ├── clock.py            # '오후 9시 30분' 같은 시각 입력 해석
│   │   ├── texts.py            # 공통 안내문
│   │   ├── logging_setup.py    # 로깅 + 토큰 마스킹 필터
│   │   └── broadcast/          # models, flow(/broadcast FSM), sender(발송 워커), report(/adstats)
│   ├── config/
│   │   ├── common.toml         # 필수 채널, 캐시 TTL, 게이트 fail 모드, 레이트리밋 (모든 봇 공통)
│   │   └── common.example.toml
│   ├── testkit/                # 가짜 Bot API 서버(fake_api.py)와 pytest 픽스처(fixtures.py)
│   ├── tests/
│   └── requirements.txt, requirements-dev.txt
├── kakao/                      # 봇: 이모티콘샵 링크 → 텔레그램 스티커 세트 (서버: /root/kakao 링크)
│   ├── main.py                 # run_bot(ROOT, routers=[...]); ../shared 를 import 경로에 추가
│   ├── bot/                    # kakao.py, media.py, stickers.py, login.py, services.py, texts.py, handlers/
│   ├── data/                   # bot.db, session.json (gitignore)
│   ├── tests/
│   ├── .env.example            # BOT_TOKEN, ADMIN_IDS, KAKAO_SESSION_PATH, STICKER_EMOJI
│   ├── ecosystem.config.js     # pm2 앱 `kakao`
│   └── requirements.txt, requirements-login.txt
├── boost/                      # 봇: 그룹·채널 부스트 판매 (서버: /root/boost 링크)
│   ├── main.py, bot/           # orders.py(주문 상태), payments.py(입금 매칭), tron.py, smm.py, fulfil.py, worker.py, levels.py(계산기), handlers/
│   ├── config/products.toml    # 상품(패널 서비스 ID·개수·가격)과 레벨 계산 규칙
│   ├── .env.example            # BOT_TOKEN, ADMIN_IDS, WALLET_ADDRESS, TRONGRID_API_KEY, SMM_API_URL/KEY, ORDER_TTL_MIN
│   └── ecosystem.config.js     # pm2 앱 `boost`
├── rankbot/                    # 봇: 채팅 순위 봇 메이커 (서버: /root/rankbot 링크)
│   ├── main.py, bot/           # fleet.py(고객 봇 묶음), child.py(그룹 집계·명령), settler.py(주간 발표), store.py, crypto.py, schedule.py, handlers/
│   ├── .env.example            # BOT_TOKEN, ADMIN_IDS, TOKEN_KEY, MAX_BOTS_PER_USER
│   └── ecosystem.config.js     # pm2 앱 `rankbot`
├── walletbot/                  # 봇: 지갑 입금 알림·잔액·시세/김프 (서버: /root/walletbot 링크)
│   ├── main.py, bot/           # chain.py(TronGrid·블록 해석), scanner.py(블록 스캔), watcher.py(확정 확인·잔액 대조·김프·정기 알림), prices.py(빗썸·환율), store.py, notify.py, handlers/
│   ├── .env.example            # BOT_TOKEN, ADMIN_IDS, TRONGRID_API_KEY, MAX_WALLETS_TOTAL, RECONCILE_MINUTES
│   └── ecosystem.config.js     # pm2 앱 `walletbot`
├── verify/                     # 봇: 채널 가입 검수 (채널 관리자, 서버: /root/verify 링크)
│   ├── main.py                 # 검수 서비스(127.0.0.1:VERIFY_PORT)를 띄움, 자기 자신에겐 게이트 없음
│   ├── bot/                    # /start, /help, 관리자용 /channels, /check
│   ├── .env.example            # BOT_TOKEN, ADMIN_IDS, VERIFY_SECRET, VERIFY_PORT
│   └── ecosystem.config.js     # pm2 앱 `verify`
├── scripts/
│   ├── setup_server.sh         # Python/pip/pm2/Node/ffmpeg 설치, ufw, 스왑, 의존성 설치
│   ├── link_bots.sh            # /root/<봇> -> Tip/<봇> 링크
│   ├── deploy.sh               # git pull → pip install → 모든 봇 pm2 reload
│   ├── backup_db.sh            # 봇별 sqlite3 .backup, 7일 보관
│   └── install_cron.sh         # 일일 백업 cron 등록
├── docs/
│   ├── PROJECT_SPEC.md
│   └── STYLE_GUIDE.md
├── .gitignore
└── README.md
```

### 새 봇을 만들 때

1. `Tip/<새봇>/`을 만들고 `kakao/main.py`, `kakao/ecosystem.config.js`, `.env.example`, `requirements.txt`를 참고해 이름만 바꾼다.
2. 봇 코드(`bot/`)를 쓴다. 공통 기능은 `run_bot(ROOT, routers=[...])` 한 줄로 붙는다.
3. `.env`를 채우고 `scripts/link_bots.sh`, `scripts/deploy.sh`로 반영한다. 공통 설정(필수 채널 등)은 `shared/config/common.toml` 한 곳에서 바꾼다.

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

## DB 스키마 (`<봇>/data/bot.db`)

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

- **pm2**: 봇별 앱 등록, 자동 재시작, 메모리 초과 시 재시작(`max_memory_restart`), pm2-logrotate, `pm2 startup` + `pm2 save`. 봇 폴더마다 `ecosystem.config.js`가 `main.py` 하나를 앱으로 등록한다. `scripts/deploy.sh`가 모든 봇 폴더를 반영한다.
- **scripts/setup_server.sh**: Python/pip/pm2/Node 설치, ufw 방화벽, 스왑, 의존성 설치
- **scripts/deploy.sh**: git pull → `pip install -r requirements.txt` → pm2 reload
- **SQLite 백업**: 매일 cron으로 각 봇 DB를 `sqlite3 .backup`으로 복사하고 7일치를 보관한다.
- **필수 채널 관리자 등록**: 봇이 필수 채널의 관리자로 추가되어야 `getChatMember`가 동작한다. README에 명시한다.

## 진행 방식

1. 폴더 구조, 주요 테이블 스키마, 구현 계획을 보여주고 승인 후 구현한다. (계획 제시 완료, 구현 승인 대기)
2. 게이트, 사용자 등록, 브로드캐스트가 실제로 동작하는지 검증한다. 실제 텔레그램은 쓸 수 없으므로 로컬 가짜 Bot API 서버(aiohttp 목)에 aiogram을 연결해 자동 테스트한다.
   - 게이트 차단/통과
   - 사용자 등록
   - 브로드캐스트 발송과 429/403 처리
   - 재시작 후 이어보내기
   - 필수 채널 시작 점검(관리자 권한 부족 감지)
   - 검수 서비스: 비밀값 인증, 가입/미가입/확인 실패 구분, 서비스 장애 시 fail_mode 적용
   - `ecosystem.config.js` 앱 정의
3. 완료 후 README에 서버 배포 절차, 새 봇 추가 방법, 광고 발송 방법을 정리한다.

### 구현 순서

1. 골격: `requirements.txt`, `.gitignore`, `core/config`, `db`, `logging`, `users`
2. 구독 게이트 + `run_bot` + 관리자 `/stats`
3. 브로드캐스트(플로우, 발송 워커, 재개) + 리포트
4. 봇 코드(`kakao/bot/`, `kakao/main.py`) + `ecosystem.config.js`
5. 운영 스크립트(`setup_server.sh`, `deploy.sh`, `backup_db.sh`, cron)
6. 자동 테스트로 검증
7. README 작성

## 의존성

`aiogram`, `aiosqlite`, `aiohttp`(카카오 조회 + 테스트용 목 서버), `python-dotenv`, `Pillow`(스티커 봇의 WebP 프레임 디코딩)를 `requirements.txt`에 버전 고정한다. 테스트 도구(`pytest`, `pytest-asyncio`)는 `requirements-dev.txt`에 둔다. 스티커 봇은 시스템 패키지 `ffmpeg`(libvpx-vp9)도 필요하다. Python 3.11+ 기준으로 `tomllib`를 쓴다.
