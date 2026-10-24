// 이 폴더의 봇 하나를 pm2로 실행한다. 공통 코드는 ../shared(통합 폴더)에서 불러온다.
const path = require("path");

const root = __dirname;

module.exports = {
  apps: [
    {
      name: "kakao",
      script: "main.py",
      interpreter: "python3",
      cwd: root,
      env: { PYTHONPATH: [root, path.join(root, "..", "shared")].join(path.delimiter), PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_memory_restart: "400M",
      // 시작 직후 계속 죽으면(토큰 누락 등) 무한 재시작하지 않도록 한다
      min_uptime: "10s",
      max_restarts: 20,
      restart_delay: 3000,
    },
  ],
};
