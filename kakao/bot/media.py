"""카카오 이미지를 텔레그램 스티커 규격으로 변환한다.

- 움직이는 이미지 → 비디오 스티커: WEBM(VP9, 알파), 한 변 512px, 3초 이하, 30fps 이하, 256KB 이하
- 정지 이미지 → 정적 스티커: 한 변 512px, 512KB 이하

ffmpeg의 WebP 디코더는 애니메이션을 못 읽어서 Pillow로 프레임을 풀어 ffmpeg에 넘긴다.
"""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import zlib
from pathlib import Path

from PIL import Image, ImageSequence

SIZE = 512
MAX_SECONDS = 3.0
MAX_FPS = 30
VIDEO_LIMIT = 256 * 1024
STATIC_LIMIT = 512 * 1024
# 용량이 넘으면 화질(crf)을 낮추고, 가장 낮은 화질로도 넘으면 프레임 수를 줄인다
CRF_STEPS = (30, 36, 42, 48, 54, 60)
FPS_STEPS = (30, 24, 20, 15)
# 용량이 fps에 비례한다고 보고 추정한 값이 한도의 이 배수를 넘으면 그 fps는 건너뛴다
SKIP_MARGIN = 1.3


# ffmpeg(VP9 인코더)가 코어 수만큼 스레드를 띄워 메모리를 더 쓰므로 상한을 둔다. 환경변수로 바꿀 수 있다.
FFMPEG_THREADS = int(os.environ.get("FFMPEG_THREADS") or 2)


class ConvertError(Exception):
    pass


class Frame:
    """512px RGBA 프레임 하나. 원본 픽셀은 압축해서 들고 있다.

    투명한 이모티콘은 압축이 잘 돼서 프레임당 1MB(512x512x4)가 100~200KB 정도로 줄고,
    프레임이 수십~백 개인 이모티콘 하나가 메모리를 수백MB 먹던 문제를 막는다.
    """

    __slots__ = ("size", "_z")

    def __init__(self, im: Image.Image):
        self.size = im.size
        self._z = zlib.compress(im.tobytes(), 1)

    def raw(self) -> bytes:
        return zlib.decompress(self._z)

    def image(self) -> Image.Image:
        return Image.frombytes("RGBA", self.size, self.raw())


def _fit(im: Image.Image) -> Image.Image:
    """긴 변이 정확히 512가 되게 맞춘다. 다른 변은 비율대로(짝수)."""
    w, h = im.size
    scale = SIZE / max(w, h)
    nw, nh = max(2, round(w * scale)), max(2, round(h * scale))
    nw, nh = nw + nw % 2, nh + nh % 2
    nw, nh = min(nw, SIZE), min(nh, SIZE)
    return im.convert("RGBA").resize((nw, nh), Image.LANCZOS)


def load_frames(data: bytes) -> tuple[list[Frame], list[float]]:
    """(프레임들, 프레임별 길이(초))"""
    try:
        im = Image.open(io.BytesIO(data))
        frames, durations = [], []
        for fr in ImageSequence.Iterator(im):
            d = fr.info.get("duration", im.info.get("duration", 100)) or 100
            frames.append(Frame(_fit(fr)))      # 한 프레임씩 압축해 바로 내려놓는다
            durations.append(max(d, 10) / 1000)
    except Exception as e:  # noqa: BLE001 - Pillow는 포맷마다 다른 예외를 던진다
        raise ConvertError(f"이미지를 열지 못했어요: {e}") from e
    if not frames:
        raise ConvertError("프레임이 없어요.")
    return frames, durations


def resample(frames: list[Frame], durations: list[float], fps: int) -> list[Frame]:
    """가변 길이 프레임을 고정 fps로 펴고, 3초를 넘으면 재생 속도를 올려 맞춘다(자르지 않는다)."""
    total = sum(durations)
    target = min(total, MAX_SECONDS - 0.05)
    n = max(1, int(target * fps))
    step = total / n  # 원본 시간 기준 한 칸
    out, idx = [], 0
    ends = []
    acc = 0.0
    for d in durations:
        acc += d
        ends.append(acc)
    for k in range(n):
        t = k * step
        while idx < len(ends) - 1 and t >= ends[idx]:
            idx += 1
        out.append(frames[idx])
    return out


def _encode(frames: list[Frame], fps: int, crf: int) -> bytes:
    """프레임을 ffmpeg에 하나씩 흘려보낸다. 전체를 한 덩어리로 이어붙이면 프레임 수만큼 메모리가 더 든다."""
    w, h = frames[0].size
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.webm"
        err_path = Path(tmp) / "err.txt"
        cmd = [
            "nice", "-n", "10",                    # 변환이 CPU를 다 먹어도 서버 나머지(봇 응답, SSH)가 느려지지 않게
            "ffmpeg", "-y", "-loglevel", "error", "-threads", str(FFMPEG_THREADS),
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(crf),
            "-auto-alt-ref", "0", "-deadline", "good", "-cpu-used", "5", "-an", "-t", f"{MAX_SECONDS}",
            str(out),
        ]
        with open(err_path, "wb") as err:
            # start_new_session: 터미널/프로세스 그룹에 오는 SIGINT가 ffmpeg까지 죽이지 않게 한다
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err, start_new_session=True)
            try:
                last_img, last_raw = None, b""
                for f in frames:
                    if f is not last_img:          # 같은 프레임이 이어지면 압축 푼 결과를 재사용
                        last_img, last_raw = f, f.raw()
                    p.stdin.write(last_raw)
                p.stdin.close()
            except BrokenPipeError:
                pass                                # ffmpeg가 먼저 끝난 경우: 아래에서 종료 코드로 판단
            finally:
                if not p.stdin.closed:
                    p.stdin.close()
            rc = p.wait()
        if rc != 0 or not out.exists():
            tail = err_path.read_bytes().decode(errors="replace")[-200:].strip()
            raise ConvertError(f"영상 변환에 실패했어요 (ffmpeg 종료 코드 {rc}): {tail}")
        return out.read_bytes()


def to_video_sticker(data: bytes) -> bytes:
    """256KB 안에 들어가는 가장 좋은 화질을 찾는다.

    인코딩 한 번이 CPU를 몇 초씩 먹으므로 횟수를 줄이는 게 핵심이다.
    가장 낮은 화질로 먼저 인코딩해서 이 fps로 가능한지 보고(화질이 좋을수록 용량이 몇 배로 커져서 가장 비싸다),
    가능하면 그 사이를 이분 탐색해 들어가는 가장 좋은 화질을 찾는다.
    불가능하면 용량이 fps에 거의 비례한다는 점을 써서, 줄여도 소용없는 fps는 인코딩 없이 건너뛴다.
    """
    frames, durations = load_frames(data)
    last, last_fps = b"", FPS_STEPS[0]
    for i, fps in enumerate(FPS_STEPS):
        if last and len(last) * fps / last_fps > VIDEO_LIMIT * SKIP_MARGIN and i < len(FPS_STEPS) - 1:
            continue
        seq = resample(frames, durations, fps)
        best = _encode(seq, fps, CRF_STEPS[-1])
        if len(best) > VIDEO_LIMIT:
            last, last_fps = best, fps
            continue
        lo, hi = -1, len(CRF_STEPS) - 1                  # lo는 넘치는 쪽(-1은 시도 안 한 최고 화질), hi는 들어가는 쪽
        while hi - lo > 1:
            mid = (lo + hi) // 2
            out = _encode(seq, fps, CRF_STEPS[mid])
            if len(out) <= VIDEO_LIMIT:
                best, hi = out, mid
            else:
                lo = mid
        return best
    raise ConvertError(f"256KB 안에 못 담았어요 (마지막 {len(last) // 1024}KB).")


def to_static_sticker(data: bytes) -> bytes:
    frames, _ = load_frames(data)
    im = frames[0].image()
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    if buf.tell() <= STATIC_LIMIT:
        return buf.getvalue()
    for q in (90, 80, 70, 60):
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=q)
        if buf.tell() <= STATIC_LIMIT:
            return buf.getvalue()
    raise ConvertError("512KB 안에 못 담았어요.")
