"""카카오 로그인 1회성 스크립트 (kakaot/login.js의 Python 포팅).

터미널에 아이디/비밀번호(+필요 시 인증번호)를 입력하면 headless 브라우저가 로그인하고,
세션(쿠키)을 session.json으로 저장한다. 봇은 이 파일을 요청마다 읽어서 재사용한다.

사용: python3 -m bot.login      (프로젝트 루트에서)
설치: pip install --break-system-packages -r requirements-login.txt && python3 -m playwright install chromium
"""
from __future__ import annotations

import asyncio
import getpass
import json
import re
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
LOGIN_URL = "https://accounts.kakao.com/login?continue=https%3A%2F%2Fe.kakao.com%2F"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ID_SELECTORS = ["#loginId--1", 'input[name="email"]', 'input[type="text"]:visible']
PW_SELECTORS = ["#password--2", 'input[name="password"]', 'input[type="password"]:visible']
SUBMIT_SELECTORS = ["button.submit", 'button[type="submit"]']
EXTRA_INPUT_SELECTOR = 'input[type="text"]:visible, input[type="tel"]:visible, input[type="number"]:visible'
CONTINUE_SELECTOR = "button:visible, a:visible"
ERROR_SELECTORS = ['[class*="error"]:visible', '[class*="msg_error"]:visible', '[role="alert"]:visible']


def session_path() -> Path:
    env = dotenv_values(ROOT / ".env")
    return Path(env.get("KAKAO_SESSION_PATH") or ROOT / "data" / "session.json")


def is_logged_in_url(url: str) -> bool:
    return bool(re.match(r"^https://([\w-]+\.)?kakao\.com/", url)) and "accounts.kakao.com" not in url


async def _visible(loc) -> bool:
    try:
        return await loc.is_visible()
    except Exception:  # noqa: BLE001 - 페이지 전환 중 오류는 "안 보임"으로 취급
        return False


async def fill_first(page, selectors, value, retries=15, delay=300) -> bool:
    for _ in range(retries):
        for sel in selectors:
            loc = page.locator(sel).first
            if await _visible(loc):
                await loc.fill(value)
                return True
        await page.wait_for_timeout(delay)
    return False


async def click_first(page, selectors, retries=5, delay=300, has_text=None) -> bool:
    for _ in range(retries):
        for sel in selectors:
            loc = page.locator(sel)
            loc = (loc.filter(has_text=has_text) if has_text else loc).first
            if await _visible(loc):
                await loc.click()
                return True
        await page.wait_for_timeout(delay)
    return False


SKIP_TEXTS = ("다음에 변경하기", "다음에 하기", "나중에 변경", "나중에 하기", "건너뛰기")


async def dismiss_interstitial(page) -> bool:
    """로그인 직후 뜨는 안내 화면(비밀번호 변경 권유 등)에서 '다음에 ...'을 눌러 넘긴다."""
    for text in SKIP_TEXTS:
        loc = page.locator("button:visible, a:visible").filter(has_text=text).first
        if await _visible(loc):
            print(f"안내 화면이 떠서 '{text}'를 눌렀어요.")
            await loc.click()
            await settle(page)
            return True
    return False


async def any_visible(page, selectors) -> bool:
    return any([await _visible(page.locator(s).first) for s in selectors])


def is_real_error(text: str) -> bool:
    """클래스 이름에 error가 들어간 요소 중에는 안내 문구나 주소 문자열도 섞여 있어서 걸러낸다."""
    text = text.strip()
    if not text or len(text) > 200:
        return False
    if text.startswith(("TIP", "/", "http", "#")):   # 입력 안내("TIP ..."), 주소
        return False
    return True


async def error_text(page) -> str:
    for sel in ERROR_SELECTORS:
        try:
            locs = await page.locator(sel).all()
        except Exception:  # noqa: BLE001
            continue
        for loc in locs:
            if not await _visible(loc):
                continue
            try:
                text = (await loc.inner_text()).strip()
            except Exception:  # noqa: BLE001
                continue
            if is_real_error(text):
                return text
    return ""


async def dump_text(page) -> None:
    """실패 원인을 알 수 있게 화면에 보이는 글자를 일부 출력한다."""
    try:
        body = (await page.inner_text("body")).strip()
        print(f"[디버그] 화면 글자: {' '.join(body.split())[:400]}")
    except Exception:  # noqa: BLE001
        pass


async def settle(page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001 - 끝까지 안 조용해도 진행한다
        pass


async def dump_inputs(page, label: str) -> None:
    """화면 구조가 바뀌어 실패할 때 원인을 볼 수 있게 input 태그를 출력한다."""
    try:
        inputs = await page.evaluate(
            """() => Array.from(document.querySelectorAll("input")).map(el => ({
                id: el.id, name: el.name, type: el.type, placeholder: el.placeholder,
                visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)}))"""
        )
        print(f"[디버그] {label} - 현재 URL: {page.url}")
        print(f"[디버그] 페이지의 input 태그들: {json.dumps(inputs, ensure_ascii=False)}")
    except Exception:  # noqa: BLE001 - 진단 실패는 무시
        pass


ACCOUNT_URL = "https://accounts.kakao.com/weblogin/account"


async def verify_logged_in(page) -> bool:
    """안내 화면을 넘긴 뒤 판정한다.

    e.kakao.com은 로그인 없이도 열려서 판정에 쓸 수 없다. 대신 로그인해야만 열리는 계정 페이지가
    로그인 화면(/login)으로 튕기지 않으면 로그인된 것으로 본다.
    """
    for _ in range(3):
        if not await dismiss_interstitial(page):
            break
    if is_logged_in_url(page.url):
        return True
    try:
        await page.goto(ACCOUNT_URL, wait_until="domcontentloaded")
        await settle(page)
    except Exception:  # noqa: BLE001
        return False
    return "/login" not in page.url


async def wait_for_confirmation(page, total_ms=90000, interval_ms=3000) -> tuple[bool, str]:
    """카카오톡 앱 '로그인 승인'처럼 화면 변화 없이 휴대폰 승인을 기다리는 경우를 위해 길게 본다."""
    waited = 0
    while waited < total_ms:
        if is_logged_in_url(page.url):
            return True, ""
        if await dismiss_interstitial(page):
            continue
        err = await error_text(page)
        if err:
            return False, err
        await page.wait_for_timeout(interval_ms)
        waited += interval_ms
        if waited % 15000 == 0:
            print(f"  ...승인 기다리는 중 ({waited // 1000}초)")
    return is_logged_in_url(page.url), ""


async def run(out: Path) -> int:
    from playwright.async_api import async_playwright

    print("카카오 로그인을 진행할게요. 아이디 / 비밀번호 / (필요 시) 인증번호를 입력하면 자동으로 마칩니다.\n")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            await dump_inputs(page, "로그인 페이지 로드 직후")

            email = (await asyncio.to_thread(input, "카카오 아이디 (이메일 또는 전화번호): ")).strip()
            if not await fill_first(page, ID_SELECTORS, email):
                await dump_inputs(page, "아이디 입력란 탐색 실패")
                raise RuntimeError("아이디 입력란을 찾지 못했어요. 카카오 로그인 화면 구조가 바뀌었을 수 있어요.")

            password = await asyncio.to_thread(getpass.getpass, "비밀번호: ")
            if not await fill_first(page, PW_SELECTORS, password):
                await dump_inputs(page, "비밀번호 입력란 탐색 실패")
                raise RuntimeError("비밀번호 입력란을 찾지 못했어요. 카카오 로그인 화면 구조가 바뀌었을 수 있어요.")

            await click_first(page, SUBMIT_SELECTORS)
            await settle(page)
            await dump_inputs(page, "1차 로그인 제출 후")

            if not is_logged_in_url(page.url) and await any_visible(page, PW_SELECTORS):
                err = await error_text(page)
                if err:
                    print("\n로그인이 처리되지 않고 같은 화면에 머물러 있어요 (아이디/비밀번호를 다시 확인해 주세요).")
                    print(f"화면에 표시된 메시지: {err}")
                else:
                    print("\n카카오톡 앱으로 로그인 승인 요청이 갔을 수 있어요. 휴대폰 알림에서 '로그인 승인'을 눌러주세요.")
                    print("최대 90초까지 기다릴게요...")
                    confirmed, err = await wait_for_confirmation(page)
                    await dump_inputs(page, "카카오톡 승인 대기 후")
                    if err:
                        print(f"\n로그인이 처리되지 않았어요.\n화면에 표시된 메시지: {err}")
                    elif not confirmed:
                        print("\n90초 동안 로그인 승인을 확인하지 못했어요. 휴대폰에서 승인을 눌렀는지 확인하고 다시 시도해 주세요.")
            else:
                # 추가 인증(문자/카카오톡 인증번호 등)은 최대 3회까지 입력받는다
                for attempt in range(3):
                    if is_logged_in_url(page.url) or await any_visible(page, PW_SELECTORS):
                        break
                    extra = page.locator(EXTRA_INPUT_SELECTOR).first
                    if not await _visible(extra):
                        break
                    print("\n추가 인증이 필요해요. 문자/카카오톡으로 받은 인증번호 등 화면에서 요구하는 값을 입력해 주세요.")
                    value = (await asyncio.to_thread(input, "입력값: ")).strip()
                    await extra.fill(value)
                    await click_first(page, SUBMIT_SELECTORS)
                    await settle(page)
                    await dump_inputs(page, f"추가 인증 {attempt + 1}회차 제출 후")
                # "계속/확인/동의" 안내 화면이 남아 있으면 한 번 더 통과 시도
                if not is_logged_in_url(page.url):
                    await click_first(page, [CONTINUE_SELECTOR], retries=1, has_text=re.compile("확인|계속|동의|허용"))
                    await settle(page)

            ok = await verify_logged_in(page)
            if not ok:
                await dump_text(page)
                print("\n로그인 완료를 확인하지 못했어요. 로그인 안 된 세션이 봇에 쓰이면 안 돼서 저장하지 않았어요.")
                print("승인 알림/인증번호를 놓쳤다면 다시 실행해 주세요.")
                return 1

            print("\n로그인에 성공했어요.")
            state = await context.storage_state()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf8")
            out.chmod(0o600)  # 로그인 쿠키가 들어 있다
            print(f"세션을 저장했어요 -> {out}")
            return 0
        finally:
            await browser.close()


def main() -> int:
    try:
        return asyncio.run(run(session_path()))
    except ImportError:
        print("playwright가 없어요. pip install --break-system-packages -r requirements-login.txt && python3 -m playwright install chromium")
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"로그인 중 오류가 발생했어요: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
