#!/usr/bin/env bash
# git pull → 의존성 설치 → 모든 봇을 새 설정으로 다시 등록해 시작
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git pull --ff-only
pip install --break-system-packages -r shared/requirements.txt
for req in */requirements.txt; do
  [ "$(dirname "$req")" = "shared" ] && continue
  pip install --break-system-packages -r "$req"
done

# 봇 폴더(main.py + ecosystem.config.js)마다: 지우고 다시 시작한다.
# reload/restart는 pm2에 저장된 옛 옵션(메모리 한도, kill_timeout 등)을 그대로 쓸 수 있어서,
# ecosystem.config.js의 변경을 확실히 반영하려고 다시 등록한다. delete는 봇이 진행 중인 작업을
# 마칠 때까지(최대 kill_timeout) 기다린 뒤 종료한다.
for eco in */ecosystem.config.js; do
  for name in $(node -e "require('./$eco').apps.forEach(a => console.log(a.name))"); do
    pm2 delete "$name" 2>/dev/null || true
  done
  pm2 start "$eco"
done
pm2 save
