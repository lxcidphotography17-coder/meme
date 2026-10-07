# Memecoin Desk

A paper-trading Solana memecoin desk (`memecoin_desk.py`). Scans DexScreener for
token leads, runs them through RugCheck risk clearance, confirms buy pressure,
then paper-trades with stop-loss / trailing-stop / scale-out exit logic. All
trade history is stored in a local SQLite file (`desk.db`).

## Running in the Base44 sandbox

```bash
docker compose -f docker-compose.base44.yml up -d --build
```

- The `app` service (python:3.12-slim) bind-mounts the repo and installs
  `requirements.txt` (requests, flask) on startup.
- `start.sh` launches the trading bot in the background (stdout → `desk.log`)
  and a read-only Flask dashboard (`desk_web.py`) on port 3000 in the foreground.
- The dashboard reads `desk.db` and `desk.log` — it does **not** modify the bot.
- Health: `GET http://localhost:3000/` (served by the dashboard).

## Notes

- The bot runs in **paper mode** by default (`Cfg.paper = True`). No private keys
  are used; live signing is intentionally not implemented.
- External APIs called: DexScreener (public, no key) and RugCheck (public, no key).
- Optional: set `TG_TOKEN` and `TG_CHAT_ID` env vars for Telegram trade alerts.
  These are **not** required — the desk runs without them.
- `desk.db` and `desk.log` are created at runtime in the repo root.
