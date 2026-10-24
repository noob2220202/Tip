#!/usr/bin/env bash
# data/bot.db를 sqlite3 .backup으로 복사하고 7일치만 보관한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/data/bot.db"
DEST="$ROOT/backups"

[ -f "$DB" ] || exit 0
mkdir -p "$DEST"
sqlite3 "$DB" ".backup '$DEST/bot-$(date +%Y%m%d).db'"
find "$DEST" -name 'bot-*.db' -mtime +7 -delete
