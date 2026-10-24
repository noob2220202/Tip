#!/usr/bin/env bash
# 새 Ubuntu/Debian 서버 1회 설정: Python/pip/ffmpeg/sqlite3/Node/pm2, ufw, 스왑, 의존성.
# 사용: sudo bash scripts/setup_server.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update
apt-get install -y python3 python3-pip sqlite3 ffmpeg ufw curl git cron nodejs npm

# pm2 + 로그 로테이션
npm install -g pm2
pm2 install pm2-logrotate

# 시스템 Python에 직접 설치 (venv/Docker 사용 안 함)
pip install --break-system-packages -r "$ROOT/requirements.txt"

# 방화벽: SSH만 연다 (봇은 롱폴링이라 인바운드 포트가 필요 없다)
ufw allow OpenSSH
ufw --force enable

# 스왑 1GB (없을 때만)
if ! swapon --show | grep -q .; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "끝났어요. 다음: pm2 startup 이 알려주는 명령을 한 번 실행하고, 봇 .env를 채운 뒤 scripts/deploy.sh"
