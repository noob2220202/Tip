// bots/*/main.py를 스캔해 pm2 앱 목록을 만든다. 새 봇 폴더만 만들면 `pm2 reload ecosystem.config.js`로 반영된다.
const fs = require("fs");
const path = require("path");

const root = __dirname;
const botsDir = path.join(root, "bots");

const apps = fs
  .readdirSync(botsDir, { withFileTypes: true })
  .filter((d) => d.isDirectory() && fs.existsSync(path.join(botsDir, d.name, "main.py")))
  .map((d) => ({
    name: d.name,
    script: path.join("bots", d.name, "main.py"),
    interpreter: "python3",
    cwd: root,
    env: { PYTHONPATH: root, PYTHONUNBUFFERED: "1" },
    autorestart: true,
    max_memory_restart: "400M",
    // 시작 직후 계속 죽으면(토큰 누락 등) 무한 재시작하지 않도록 한다
    min_uptime: "10s",
    max_restarts: 20,
    restart_delay: 3000,
  }));

module.exports = { apps };
