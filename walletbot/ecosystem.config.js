// 이 폴더의 봇 하나를 pm2로 실행한다. 공통 코드는 ../shared(통합 폴더)에서 불러온다.
const path = require("path");

const root = __dirname;

module.exports = {
  apps: [
    {
      name: "walletbot",
      script: "main.py",
      interpreter: "python3",
      cwd: root,
      env: { PYTHONPATH: [root, path.join(root, "..", "shared")].join(path.delimiter), PYTHONUNBUFFERED: "1" },
      autorestart: true,
      // 종료/재시작 때 진행 중인 요청이 끝나기를 기다린다(봇이 최대 120초 대기). pm2 기본값 1.6초면 중간에 강제 종료된다.
      kill_timeout: 130000,
      // 메모리 한도로 인한 자동 재시작은 쓰지 않는다(max_memory_restart 없음).
      // 시작 직후 계속 죽으면(토큰 누락 등) 무한 재시작하지 않도록 한다
      min_uptime: "10s",
      max_restarts: 20,
      restart_delay: 3000,
    },
  ],
};
