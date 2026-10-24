// 이 프로젝트의 봇 하나를 pm2로 실행한다. 다른 봇은 /root 아래 별도 폴더에서 각자 돌린다.
const path = require("path");

const root = __dirname;

module.exports = {
  apps: [
    {
      name: "kakao",
      script: "main.py",
      interpreter: "python3",
      cwd: root,
      env: { PYTHONPATH: root, PYTHONUNBUFFERED: "1" },
      autorestart: true,
      max_memory_restart: "400M",
      // 시작 직후 계속 죽으면(토큰 누락 등) 무한 재시작하지 않도록 한다
      min_uptime: "10s",
      max_restarts: 20,
      restart_delay: 3000,
    },
  ],
};
