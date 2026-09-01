# Replit run notes

## Start

The production entry point is `main.py` and the configured workflow runs:

```bash
python main.py
```

The HTTP server listens on `PORT` (default `8080`) and exposes `/`, `/health`,
`/ping`, and `/status`. `/` returns `Replit Telegram bot is running`, `/health`
returns `OK`, and `/ping` returns `pong`. Required values belong in Replit Secrets: `BOT_TOKEN`, `API_ID`,
`API_HASH`, `OWNER_ID`, and `MONGO_DB`.

## Deployment

The `Start application` workflow is the single combined Telegram bot and health
server process. The bot can start in temporary in-memory mode if MongoDB is
temporarily unreachable and retries the database connection in the background.