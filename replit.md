# Replit run notes

## Start

The production entry point is `main.py` and the configured workflow runs:

```bash
python main.py
```

The HTTP server listens on `PORT` (default `8000`) and exposes `/`, `/health`,
and `/status`. Required values belong in Replit Secrets: `BOT_TOKEN`, `API_ID`,
`API_HASH`, `OWNER_ID`, and `MONGO_DB`.

## Deployment

The `Start application` workflow is the single combined Telegram bot and health
server process. The bot can start in temporary in-memory mode if MongoDB is
temporarily unreachable and retries the database connection in the background.