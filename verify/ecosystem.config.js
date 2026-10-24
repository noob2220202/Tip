// 채널 가입 검수 봇. 공통 코드는 ../shared(통합 폴더)에서 불러온다.
const path = require("path");

const root = __dirname;

module.exports = {
  apps: [
    {
      name: "verify",
      script: "main.py",
      interpreter: "python3",
      cwd: root,
      env: { PYTHONPATH: [root, path.join(root, "..", "shared")].join(path.delimiter), PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_memory_restart: "300M",
      min_uptime: "10s",
      max_restarts: 20,
      restart_delay: 3000,
    },
  ],
};
