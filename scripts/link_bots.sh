#!/usr/bin/env bash
# 봇 폴더를 /root 아래에 링크한다:  /root/kakao -> <저장소>/kakao
# 사용: bash scripts/link_bots.sh [링크를 둘 폴더, 기본 /root]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-/root}"

for dir in "$ROOT"/*/; do
  name="$(basename "$dir")"
  [ -f "$dir/main.py" ] && [ -f "$dir/ecosystem.config.js" ] || continue   # 봇 폴더만
  ln -sfn "$ROOT/$name" "$DEST/$name"
  echo "$DEST/$name -> $ROOT/$name"
done
