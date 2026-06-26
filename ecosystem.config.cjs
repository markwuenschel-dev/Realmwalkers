// pm2 process for the Writers' Desk dev server. The desk is a Next.js app, so the `desk` process now
// runs `next dev` (the prototype's Vite server is gone). Start/refresh it with:
//   pm2 start ecosystem.config.cjs      # first run
//   pm2 restart desk                    # after pulling changes
// The backend (FastAPI :8000) is run separately via dev.sh in WSL; the Next BFF (/api/desk/*) proxies
// to it via API_BASE.
module.exports = {
  apps: [
    {
      name: "desk",
      cwd: "./frontend",
      script: "node_modules/next/dist/bin/next",
      args: "dev -H 0.0.0.0 -p 3000",
      autorestart: true,
      env: {
        API_BASE: "http://127.0.0.1:8000",
      },
    },
  ],
};
