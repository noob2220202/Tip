# kakao — 카카오 이모티콘 → 텔레그램 스티커 봇

카카오 이모티콘샵 링크를 붙여넣으면 텔레그램 스티커 세트로 만들어 주는 봇입니다. 세트 제목은 항상 `@봇이름ㅣ이모티콘이름`(예: `@TIP_STICKERS_botㅣ귀여운 비둘기`)입니다. 공통 기능(채널 가입 검증, 사용자 DB, 관리자, 브로드캐스트)은 [`../shared`](../shared)를 씁니다. 전체 배포 절차는 [루트 README](../README.md)를 보세요.

```
kakao/
├── main.py            # run_bot(ROOT, ...) — ../shared 를 import 경로에 추가
├── bot/               # 카카오 조회, 변환, 세트 생성, 로그인, 문구, 핸들러
├── data/              # bot.db, session.json (gitignore)
├── .env.example  ecosystem.config.js  requirements*.txt
└── tests/
```

## 동작

유저가 `e.kakao.com/t/…` 또는 `emoticon.kakao.com/items/…` 링크를 보내면:

1. 카카오 API로 팩을 조회하고 이미지 종류(움직임/정지)를 판별합니다.
2. 움직이는 이모티콘은 **비디오 스티커**(WEBM VP9, 알파 유지, 긴 변 512px, 30fps 이하, 256KB 이하)로 변환합니다. 3초를 넘으면 자르지 않고 재생 속도를 올려 맞춥니다. 정지 이미지는 512px **정적 스티커**로 만듭니다.
3. 세트 제목 `@봇이름ㅣ이모티콘이름`(64자 초과 시 이름을 자름)으로 세트를 만들고 `t.me/addstickers/…` 링크를 돌려줍니다. 세트 URL 이름은 `tip_<유저ID>_<키>_by_<봇username>`입니다.
4. 이미 만든 이모티콘이면 **카카오에 접속하지 않고 바로** 기존 링크를 줍니다. 다른 사용자가 만든 세트도 재사용하고, 링크 형태가 달라도(`e.kakao.com/t/…` ↔ `emoticon.kakao.com/items/…`) 같은 이모티콘이면 이미지 목록으로 알아봅니다. 세트가 텔레그램에서 지워졌으면 다시 만듭니다.

제약:

- 한 세트에는 같은 형식만 들어갑니다. 움직이는 것과 정지가 섞인 팩은 움직이는 것만 담고 안내합니다.
- 한 세트 최대 120개.
- 움직이는 이모티콘은 카카오 로그인 세션이 있어야 받아집니다. 세션이 없으면 정지 이미지로 대신 만들지 않고, 유저에게 점검 중이라고 안내하며 관리자에게 시간당 한 번 알립니다.

### 카카오 세션 준비 (1회성 로그인)

서버에서 직접 로그인합니다. 화면(X서버)이 없어도 headless 브라우저로 동작합니다.

```bash
pip install --break-system-packages -r requirements-login.txt
python3 -m playwright install --with-deps chromium
cd /root/kakao && python3 -m bot.login
```

터미널에 아이디, 비밀번호(입력이 화면에 보이지 않음), 필요하면 인증번호를 입력하면 `data/session.json`(권한 600)이 만들어집니다. 카카오톡 앱 "로그인 승인" 알림이 오는 계정이면 휴대폰에서 승인을 눌러주세요(최대 90초 대기). 화면 구조가 바뀌어 입력란을 못 찾으면 `[디버그]` 줄에 현재 input 목록이 찍힙니다.

GUI가 있는 PC에서 [kakaot](https://github.com/noob2220202/kakaot)의 `npm run login`으로 만든 `session.json`을 같은 위치에 복사해도 됩니다.

경로를 바꾸려면 `.env`에 `KAKAO_SESSION_PATH`를 적습니다. 세션이 만료되면 로그인 스크립트를 다시 실행하면 되고, 봇 재시작은 필요 없습니다(요청마다 파일을 읽습니다). `session.json`에는 로그인 쿠키가 들어 있으니 커밋하지 마세요(`data/`는 `.gitignore`).

스티커마다 붙는 이모지는 `.env`의 `STICKER_EMOJI`(기본 😊)로 바꿀 수 있습니다.

## 배포 후 확인

```bash
pm2 logs kakao --lines 30      # 'kakao 시작 (@봇이름)' 확인
```
