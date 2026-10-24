#!/usr/bin/env bash
# 봇별 SQLite DB를 sqlite3 .backup으로 복사하고 7일치만 보관한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d)"

for db in "$ROOT"/bots/*/data/bot.db; do
  [ -f "$db" ] || continue
  bot="$(basename "$(dirname "$(dirname "$db")")")"
  dest="$ROOT/backups/$bot"
  mkdir -p "$dest"
  sqlite3 "$db" ".backup '$dest/bot-$STAMP.db'"
  find "$dest" -name 'bot-*.db' -mtime +7 -delete
done
