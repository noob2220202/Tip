# kakao — 카카오 이모티콘 → 텔레그램 스티커 봇

카카오 이모티콘샵 링크를 붙여넣으면 텔레그램 스티커 세트로 만들어 주는 봇입니다. 세트 제목은 항상 `TIPㅣ이모티콘이름`입니다. 구조와 규칙은 [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md), 문구 규칙은 [docs/STYLE_GUIDE.md](docs/STYLE_GUIDE.md)에 있습니다.

- Python 3.11+ / aiogram 3 / aiosqlite, 롱폴링
- Docker·venv 없이 시스템 Python에 직접 설치
- 서버는 전용 VPS. **프로젝트 하나 = `/root` 아래 폴더 하나** (이 프로젝트는 `/root/kakao`)
- DB는 `data/bot.db` (WAL)

## 서버 배포

```bash
git clone -b ccr-40e8c943-gs8iqn https://github.com/noob2220202/Tip.git /root/kakao
cd /root/kakao
sudo bash scripts/setup_server.sh     # Python/pip/ffmpeg/sqlite3/Node/pm2, ufw, 스왑, 의존성
pm2 startup                            # 출력되는 명령을 1회 실행

cp .env.example .env                   # BOT_TOKEN, ADMIN_IDS 입력
cp config/common.example.toml config/common.toml   # 필수 채널 등 (없으면 기본값으로 동작)

# 카카오 로그인 (1회) — 아래 "카카오 세션 준비" 참고
pip install --break-system-packages -r requirements-login.txt
python3 -m playwright install --with-deps chromium
python3 -m bot.login

bash scripts/deploy.sh                 # pm2 앱 `kakao` 등록·시작 + pm2 save
bash scripts/install_cron.sh           # 매일 04:10 DB 백업 (7일 보관)
```

이후 업데이트는 `bash scripts/deploy.sh` 한 번이면 됩니다 (git pull → pip install → pm2 reload). 로그는 `pm2 logs kakao`로 봅니다. 토큰이 없으면 시작하자마자 종료되고, 20번까지만 재시작합니다.

## 다른 봇 만들기

다른 봇은 이 폴더에 넣지 않고 `/root/<새봇>/`에서 따로 돌립니다.

1. `core/`, `config/`, `scripts/`, `ecosystem.config.js`, `requirements.txt`를 새 폴더로 복사합니다.
2. `main.py`와 봇 코드를 새로 씁니다. 공통 기능은 한 줄로 붙습니다.

```python
run_bot(ROOT, routers=[router], texts=TEXTS, schema=SCHEMA, on_startup=on_startup)   # schema, on_startup은 필요할 때만
```

3. `.env`를 채우고 `ecosystem.config.js`의 앱 이름을 바꾼 뒤 `pm2 start ecosystem.config.js`.

## 공통 설정 (`config/common.toml`)

| 항목 | 설명 |
|---|---|
| `gate.channels` | 필수 가입 채널. `chat_id`(`@이름` 또는 숫자), `url`(가입 링크), `title` |
| `gate.cache_ttl` | 가입 확인 결과 캐시 시간(초). 기본 60 |
| `gate.fail_mode` | `getChatMember` 오류 시 `allow`(통과) 또는 `block`(차단) |
| `gate.exempt_admins` | 관리자는 게이트 면제. 기본 true |
| `broadcast.rate_per_sec` | 초당 발송 상한. 기본 25 |

**봇이 필수 채널의 관리자로 추가되어 있어야** `getChatMember`가 동작합니다. 비공개 채널은 `chat_id`에 숫자(`-100…`)를, `url`에 초대 링크를 적습니다.

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

## 동작

유저가 `e.kakao.com/t/…` 또는 `emoticon.kakao.com/items/…` 링크를 보내면:

1. 카카오 API로 팩을 조회하고 이미지 종류(움직임/정지)를 판별합니다.
2. 움직이는 이모티콘은 **비디오 스티커**(WEBM VP9, 알파 유지, 긴 변 512px, 30fps 이하, 256KB 이하)로 변환합니다. 3초를 넘으면 자르지 않고 재생 속도를 올려 맞춥니다. 정지 이미지는 512px **정적 스티커**로 만듭니다.
3. 세트 제목 `TIPㅣ이모티콘이름`(64자 초과 시 이름을 자름)으로 세트를 만들고 `t.me/addstickers/…` 링크를 돌려줍니다. 세트 URL 이름은 `tip_<유저ID>_<키>_by_<봇username>`입니다.
4. 같은 유저가 같은 이모티콘을 다시 보내면 새로 만들지 않고 기존 링크를 줍니다 (사용자가 세트를 지웠으면 다시 만듦).

제약:

- 한 세트에는 같은 형식만 들어갑니다. 움직이는 것과 정지가 섞인 팩은 움직이는 것만 담고 안내합니다.
- 한 세트 최대 120개.
- 움직이는 이모티콘은 카카오 로그인 세션이 있어야 받아집니다. 세션이 없으면 정지 이미지로 대신 만들지 않고, 유저에게 점검 중이라고 안내하며 관리자에게 시간당 한 번 알립니다.

### 카카오 세션 준비 (1회성 로그인)

서버에서 직접 로그인합니다. 화면(X서버)이 없어도 headless 브라우저로 동작합니다.

```bash
pip install --break-system-packages -r requirements-login.txt
python3 -m playwright install --with-deps chromium
python3 -m bot.login      # 프로젝트 루트에서 실행
```

터미널에 아이디, 비밀번호(입력이 화면에 보이지 않음), 필요하면 인증번호를 입력하면 `data/session.json`(권한 600)이 만들어집니다. 카카오톡 앱 "로그인 승인" 알림이 오는 계정이면 휴대폰에서 승인을 눌러주세요(최대 90초 대기). 화면 구조가 바뀌어 입력란을 못 찾으면 `[디버그]` 줄에 현재 input 목록이 찍힙니다.

GUI가 있는 PC에서 [kakaot](https://github.com/noob2220202/kakaot)의 `npm run login`으로 만든 `session.json`을 같은 위치에 복사해도 됩니다.

경로를 바꾸려면 `.env`에 `KAKAO_SESSION_PATH`를 적습니다. 세션이 만료되면 로그인 스크립트를 다시 실행하면 되고, 봇 재시작은 필요 없습니다(요청마다 파일을 읽습니다). `session.json`에는 로그인 쿠키가 들어 있으니 커밋하지 마세요(`data/`는 `.gitignore`).

스티커마다 붙는 이모지는 `.env`의 `STICKER_EMOJI`(기본 😊)로 바꿀 수 있습니다.

## 테스트

실제 텔레그램은 쓸 수 없어서, 로컬 가짜 Bot API 서버(aiohttp)에 aiogram을 연결해 검증합니다. 카카오 API/CDN도 가짜 서버로 대체합니다.

```bash
pip install --break-system-packages -r requirements-dev.txt
python3 -m pytest
```

검증 항목: 게이트 차단/통과/재개, `/start` 등록, 관리자 전용 `/stats`, 브로드캐스트 발송(429/403/중복 방지), 재시작 후 이어보내기, `ecosystem.config.js` 앱 정의, 스티커 변환·세트 생성(50개 초과, 혼합 팩, 재사용, 세션 없음).
