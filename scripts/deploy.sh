#!/usr/bin/env bash
# git pull → 의존성 설치 → 모든 봇 pm2 반영
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git pull --ff-only
pip install --break-system-packages -r shared/requirements.txt
for req in */requirements.txt; do
  [ "$(dirname "$req")" = "shared" ] && continue
  pip install --break-system-packages -r "$req"
done

# 봇 폴더(main.py + ecosystem.config.js)마다: 떠 있으면 reload, 처음이면 start
for eco in */ecosystem.config.js; do
  pm2 reload "$eco" --update-env || pm2 start "$eco"
done
pm2 save
