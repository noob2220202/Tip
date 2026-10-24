"""카카오 이미지를 텔레그램 스티커 규격으로 변환한다.

- 움직이는 이미지 → 비디오 스티커: WEBM(VP9, 알파), 한 변 512px, 3초 이하, 30fps 이하, 256KB 이하
- 정지 이미지 → 정적 스티커: 한 변 512px, 512KB 이하

ffmpeg의 WebP 디코더는 애니메이션을 못 읽어서 Pillow로 프레임을 풀어 ffmpeg에 넘긴다.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageSequence

SIZE = 512
MAX_SECONDS = 3.0
MAX_FPS = 30
VIDEO_LIMIT = 256 * 1024
STATIC_LIMIT = 512 * 1024
# 용량이 넘으면 화질(crf)을 낮추고, 그래도 넘으면 프레임 수를 줄인다
CRF_STEPS = (30, 36, 42, 48, 54, 60)
FPS_STEPS = (30, 24, 20, 15)


class ConvertError(Exception):
    pass


def _fit(im: Image.Image) -> Image.Image:
    """긴 변이 정확히 512가 되게 맞춘다. 다른 변은 비율대로(짝수)."""
    w, h = im.size
    scale = SIZE / max(w, h)
    nw, nh = max(2, round(w * scale)), max(2, round(h * scale))
    nw, nh = nw + nw % 2, nh + nh % 2
    nw, nh = min(nw, SIZE), min(nh, SIZE)
    return im.convert("RGBA").resize((nw, nh), Image.LANCZOS)


def load_frames(data: bytes) -> tuple[list[Image.Image], list[float]]:
    """(프레임들, 프레임별 길이(초))"""
    try:
        im = Image.open(io.BytesIO(data))
        frames, durations = [], []
        for fr in ImageSequence.Iterator(im):
            d = fr.info.get("duration", im.info.get("duration", 100)) or 100
            frames.append(_fit(fr))
            durations.append(max(d, 10) / 1000)
    except Exception as e:  # noqa: BLE001 - Pillow는 포맷마다 다른 예외를 던진다
        raise ConvertError(f"이미지를 열지 못했어요: {e}") from e
    if not frames:
        raise ConvertError("프레임이 없어요.")
    return frames, durations


def resample(frames: list[Image.Image], durations: list[float], fps: int) -> list[Image.Image]:
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


def _encode(frames: list[Image.Image], fps: int, crf: int) -> bytes:
    w, h = frames[0].size
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.webm"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(crf),
            "-auto-alt-ref", "0", "-deadline", "good", "-cpu-used", "4", "-an", "-t", f"{MAX_SECONDS}",
            str(out),
        ]
        raw = b"".join(f.tobytes() for f in frames)
        p = subprocess.run(cmd, input=raw, capture_output=True)
        if p.returncode != 0 or not out.exists():
            raise ConvertError("영상 변환에 실패했어요: " + p.stderr.decode(errors="replace")[-200:])
        return out.read_bytes()


def to_video_sticker(data: bytes) -> bytes:
    frames, durations = load_frames(data)
    last = b""
    for fps in FPS_STEPS:
        seq = resample(frames, durations, fps)
        for crf in CRF_STEPS:
            last = _encode(seq, fps, crf)
            if len(last) <= VIDEO_LIMIT:
                return last
    raise ConvertError(f"256KB 안에 못 담았어요 (마지막 {len(last) // 1024}KB).")


def to_static_sticker(data: bytes) -> bytes:
    frames, _ = load_frames(data)
    im = frames[0]
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
