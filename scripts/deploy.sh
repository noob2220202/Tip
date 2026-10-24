#!/usr/bin/env bash
# git pull → 의존성 설치 → pm2 반영
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git pull --ff-only
pip install --break-system-packages -r requirements.txt

# 이미 떠 있으면 reload, 처음이면 start
pm2 reload ecosystem.config.js --update-env || pm2 start ecosystem.config.js
pm2 save
