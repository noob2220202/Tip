#!/usr/bin/env bash
# 봇별 data/bot.db를 sqlite3 .backup으로 복사하고 7일치만 보관한다. (<봇>/backups/)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d)"

for db in "$ROOT"/*/data/bot.db; do
  [ -f "$db" ] || continue
  dest="$(dirname "$(dirname "$db")")/backups"
  mkdir -p "$dest"
  sqlite3 "$db" ".backup '$dest/bot-$STAMP.db'"
  find "$dest" -name 'bot-*.db' -mtime +7 -delete
done
