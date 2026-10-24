#!/usr/bin/env bash
# 매일 04:10에 DB 백업 cron 등록 (여러 번 실행해도 한 줄만 유지)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINE="10 4 * * * bash $ROOT/scripts/backup_db.sh >> $ROOT/backups/backup.log 2>&1"

mkdir -p "$ROOT/backups"
( crontab -l 2>/dev/null | grep -vF "scripts/backup_db.sh" || true; echo "$LINE" ) | crontab -
echo "등록했어요: $LINE"
