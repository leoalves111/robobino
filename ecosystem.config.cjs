/**
 * PM2 — Binomo Signal Generator (headless 24/7)
 *
 * Uso na VPS:
 *   cd /caminho/binomo-signal-generator
 *   npm install -g pm2
 *   pm2 start ecosystem.config.cjs
 *   pm2 logs binomo-signals
 *   pm2 save && pm2 startup
 */
module.exports = {
  apps: [
    {
      name: "binomo-signals",
      script: ".venv/bin/python",
      args: "main.py",
      cwd: __dirname,
      interpreter: "none",
      autorestart: true,
      max_restarts: 100,
      restart_delay: 10000,
      env: {
        HEADLESS: "true",
        LOG_LEVEL: "INFO",
      },
    },
  ],
};
