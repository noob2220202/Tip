# Tip — 텔레그램 봇 모음

전용 VPS에서 텔레그램 봇을 pm2로 운영하는 Python 프로젝트입니다. 구조와 규칙은 [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md), 문구 규칙은 [docs/STYLE_GUIDE.md](docs/STYLE_GUIDE.md)에 있습니다.

- Python 3.11+ / aiogram 3 / aiosqlite, 롱폴링
- Docker·venv 없이 시스템 Python에 직접 설치
- **공통 코드는 `shared/`(통합 폴더)에 한 번만 두고, 봇은 폴더 하나씩** 그 위에서 동작합니다.

```
Tip/                        ← 저장소 (서버에서는 /root/Tip)
├── shared/                 ← 통합 폴더: 모든 봇이 같이 씀
│   ├── core/               #   구독 게이트(채널 가입 검증), 사용자 DB, 관리자, 브로드캐스트, 로깅
│   ├── config/common.toml  #   필수 가입 채널 등 공통 설정 (한 곳에서 고치면 모든 봇에 적용)
│   ├── testkit/            #   테스트용 가짜 Bot API 서버와 pytest 픽스처
│   └── tests/
├── kakao/                  ← 봇: 카카오 이모티콘 → 텔레그램 스티커  (자세한 설명: kakao/README.md)
├── boost/                  ← 봇: 그룹·채널 부스트 판매 (USDT TRC20 결제, SMM 패널 연동)  (boost/README.md)
├── rankbot/                ← 봇: 채팅 순위 봇을 사용자가 직접 만드는 서비스 (토큰 등록 → 주간 순위 발표)  (rankbot/README.md)
├── giveaway/               ← 봇: 구독자 대상 기프트콘 추첨·전원 발송 (관리자 전용)  (giveaway/README.md)
├── walletbot/              ← 봇: TRON 지갑 입금 알림 · 잔액 · 빗썸 시세/김프 · 에너지 임대 · 비즈니스 /조회 /시세 /환율  (walletbot/README.md)
├── verify/                 ← 봇: 채널 가입 검수 전용 (채널 관리자는 이 봇 하나)
├── scripts/                ← 서버 운영 스크립트 (전체 봇 대상)
└── docs/

/root/                      ← 서버에서 봇마다 링크로 연결
├── Tip/
├── kakao -> /root/Tip/kakao
├── boost -> /root/Tip/boost
├── rankbot -> /root/Tip/rankbot
├── walletbot -> /root/Tip/walletbot
├── giveaway -> /root/Tip/giveaway
├── verify -> /root/Tip/verify
└── 다른봇 -> /root/Tip/다른봇
```

## 서버 배포

```bash
git clone -b ccr-40e8c943-gs8iqn https://github.com/noob2220202/Tip.git /root/Tip
cd /root/Tip
sudo bash scripts/setup_server.sh      # Python/pip/ffmpeg/sqlite3/Node/pm2, ufw, 스왑, 의존성
pm2 startup                             # 출력되는 명령을 1회 실행
bash scripts/link_bots.sh               # /root/kakao -> /root/Tip/kakao 링크 생성

# 검수 봇(verify)을 먼저 설정한다 — 아래 "채널 가입 검수 봇" 참고
cd /root/verify && cp .env.example .env && nano .env     # BOT_TOKEN, ADMIN_IDS, VERIFY_SECRET
cd /root/kakao  && cp .env.example .env && nano .env     # BOT_TOKEN, ADMIN_IDS, VERIFY_SECRET(verify와 같은 값)
# 카카오 로그인(1회)은 kakao/README.md 참고
cd /root/boost  && cp .env.example .env && nano .env     # 지갑·TronGrid·SMM 패널 설정은 boost/README.md 참고
cd /root/rankbot && cp .env.example .env && nano .env    # TOKEN_KEY 만드는 법은 rankbot/README.md 참고 (한 번 정하면 바꾸지 말 것)
cd /root/walletbot && cp .env.example .env && nano .env  # TronGrid 키·지갑 상한·에너지 공급처 키는 walletbot/README.md 참고
cd /root/giveaway && cp .env.example .env && nano .env   # 기프트콘 이벤트 봇: BOT_TOKEN, ADMIN_IDS, VERIFY_SECRET

cd /root/Tip
bash scripts/deploy.sh                  # pip install + 모든 봇 pm2 시작/반영 + pm2 save
bash scripts/install_cron.sh            # 매일 04:10 DB 백업 (7일 보관)
```

이후 업데이트는 `bash /root/Tip/scripts/deploy.sh` 한 번이면 됩니다 (git pull → pip install → 모든 봇을 새 설정으로 pm2에 다시 등록). 스티커를 만드는 중에 재시작하면 봇이 그 작업이 끝나기를 기다렸다가(최대 120초) 종료하므로, 반영이 조금 늦을 수 있습니다. 토큰이 없는 봇은 시작하자마자 종료되고, 20번까지만 재시작합니다.

| 스크립트 | 설명 |
|---|---|
| `scripts/setup_server.sh` | 서버 1회 설정 |
| `scripts/link_bots.sh` | 봇 폴더를 `/root/<봇>`으로 링크 |
| `scripts/deploy.sh` | git pull → 의존성 → 모든 봇을 새 설정으로 pm2에 다시 등록 |
| `scripts/backup_db.sh` | 봇별 `data/bot.db`를 `<봇>/backups/`에 백업, 7일 보관 |
| `scripts/install_cron.sh` | 일일 백업 cron 등록 |

## 채널 가입 검수 봇

필수 채널(`@GGULLTIP`)에 가입했는지는 **검수 봇(`verify`) 하나**가 확인합니다. 다른 봇들은 같은 서버의 `127.0.0.1`로 "이 유저가 가입했나?"만 물어봅니다. 그래서 채널 관리자는 검수 봇 하나이고, 새 봇을 만들어도 채널에 추가할 필요가 없습니다. (텔레그램 Bot API는 채널 전체 멤버 목록을 주지 않아서, 목록과 비교하지 않고 유저 한 명씩 `getChatMember`로 확인합니다.)

```
사용자 → kakao 봇 ──(127.0.0.1, 비밀값)──▶ verify 봇 ──getChatMember──▶ 텔레그램 채널
```

설정 순서:

1. BotFather에서 검수용 새 봇을 만들고 토큰을 받습니다.
2. **그 봇을 `@GGULLTIP` 채널의 관리자로 추가합니다.** (권한은 기본값이면 충분합니다.)
3. 비밀값을 만들어 `verify/.env`와 각 봇 `.env`의 `VERIFY_SECRET`에 **같은 값**으로 넣습니다.
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
4. `verify/.env`의 `VERIFY_PORT`(기본 8765)는 `shared/config/common.toml`의 `verifier_url` 포트와 같아야 합니다.
5. `bash scripts/deploy.sh` 후 검수 봇에게 `/channels`를 보내 `정상입니다`가 나오는지 확인합니다.

검수 서비스는 `127.0.0.1`에만 열리고 비밀값이 없으면 `401`로 거절합니다. 검수 봇이 죽어 있거나 응답이 오류면 `fail_mode`(현재 `block`)에 따라 차단되고, 봇이 시작할 때 관리자에게 DM으로 알립니다. 관리자 명령은 `/channels`(권한 점검), `/check 사용자ID`(채널별 가입 여부)입니다. `verifier_url`을 지우면 각 봇이 직접 확인하는 방식으로 돌아가며, 이때는 모든 봇이 채널 관리자여야 합니다.

## 새 봇 추가

봇은 `Tip/` 안의 폴더 하나입니다. `kakao/`를 참고해서 만듭니다.

1. `Tip/<새봇>/`에 `main.py`, `bot/`(핸들러·문구), `.env.example`, `ecosystem.config.js`, `requirements.txt`를 둡니다. `kakao/main.py`와 `kakao/ecosystem.config.js`를 복사해 이름만 바꾸면 됩니다.
2. 공통 기능은 한 줄로 붙습니다. `main.py`가 `../shared`를 import 경로에 넣어 줍니다.

```python
run_bot(ROOT, routers=[router], texts=TEXTS, schema=SCHEMA, on_startup=on_startup)   # schema, on_startup은 필요할 때만
```

3. `.env`를 채우고(`VERIFY_SECRET` 포함) `bash scripts/link_bots.sh && bash scripts/deploy.sh`. 채널 가입 확인은 `run_bot()`이 알아서 검수 봇에 맡기므로 채널 설정은 필요 없습니다.

`ecosystem.config.js`의 앱 `name`은 봇마다 겹치지 않게 합니다.

## 공통 설정 (`config/common.toml`)

설정 파일은 `shared/config/common.toml` 하나이고 모든 봇이 같이 씁니다. 현재 필수 가입 채널은 [@GGULLTIP](https://t.me/GGULLTIP)입니다. 가입하지 않은 사용자에게는 채널 가입 버튼과 `가입을 완료했습니다` 확인 버튼이 나오고, 확인을 통과하면 처음 보낸 요청을 이어서 처리합니다. 채널을 바꾸거나 늘리려면 `[[gate.channels]]` 블록을 수정·추가하고 `pm2 restart kakao`를 실행합니다.

| 항목 | 설명 |
|---|---|
| `gate.channels` | 필수 가입 채널. `chat_id`(`@이름` 또는 숫자), `url`(가입 링크), `title` |
| `gate.cache_ttl` | 가입 확인 결과 캐시 시간(초). 기본 60 |
| `gate.verifier_url` | 검수 서비스 주소(`http://127.0.0.1:8765`). 지우면 각 봇이 직접 확인 |
| `gate.fail_mode` | `getChatMember` 오류 시 `allow`(통과) 또는 `block`(차단). 현재 `block` |
| `gate.exempt_admins` | 관리자는 게이트 면제. 기본 true |
| `broadcast.rate_per_sec` | 초당 발송 상한. 기본 25 |

**검수 봇이 필수 채널의 관리자로 추가되어 있어야** `getChatMember`가 동작합니다. 봇이 시작할 때 이를 점검하고, 문제가 있으면 로그와 관리자 DM으로 알립니다. 관리자(`ADMIN_IDS`)는 게이트를 면제하므로 가입 안내를 확인하려면 일반 계정으로 시험하세요. 비공개 채널은 `chat_id`에 숫자(`-100…`)를, `url`에 초대 링크를 적습니다.

## 관리자 명령 (`.env`의 `ADMIN_IDS`만)

| 명령 | 설명 |
|---|---|
| `/stats` | 전체·활성 사용자, 오늘 신규, 최근 7일 활성 |
| `/broadcast` | 광고 작성 → 미리보기(대상 수 표시) → 발송 버튼 |
| `/campaigns` | 캠페인 목록과 상태 |
| `/adstats <ID>` | 캠페인 리포트 + 광고주에게 줄 수 있는 텍스트 파일 |
| `/bcancel <ID>` | 발송 중단 |

### 광고 발송 방법

1. `/broadcast` → 텍스트·사진·영상 중 하나를 보냅니다 (서식은 그대로 유지).
2. 버튼은 `글자 | https://주소` 형태로 한 줄에 하나, 최대 2개. 없으면 `/skip`.
3. 미리보기와 대상 수를 확인하고 **발송**을 누릅니다. 같은 캠페인은 한 번만 시작되고, 한 사용자에게 중복 발송되지 않습니다.
4. 초당 약 25건, 429는 `retry_after`만큼 쉬고 재시도, 403은 차단 처리(`is_active=0`)합니다. 봇이 재시작되면 이어서 보냅니다.

### 광고 지표

텔레그램은 노출·열람을 알려주지 않습니다. **노출은 측정할 수 없고, 대체 지표는 전달 성공 수입니다.** 리포트에는 발송 대상 / 전달 성공 / 차단·실패 / 전달률(성공 ÷ 대상)이 들어갑니다. 클릭도 추적하지 않습니다. 필요하면 광고주가 자기 쪽 UTM 링크로 따로 집계합니다.

## 테스트

실제 텔레그램은 쓸 수 없어서, 로컬 가짜 Bot API 서버(aiohttp)에 aiogram을 연결해 검증합니다. 통합 폴더와 봇이 각자 테스트를 가집니다.

```bash
pip install --break-system-packages -r shared/requirements-dev.txt
(cd shared && python3 -m pytest)    # 게이트, 브로드캐스트, 재개, 설정
(cd kakao  && python3 -m pytest)    # 스티커 봇
(cd verify && python3 -m pytest)    # 검수 봇
(cd boost  && python3 -m pytest)    # 부스트 판매 봇
(cd rankbot && python3 -m pytest)   # 채팅 순위 봇 메이커
(cd walletbot && python3 -m pytest) # 지갑 입금 알림 봇
(cd giveaway && python3 -m pytest)  # 기프트콘 이벤트 봇
```
