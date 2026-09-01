# AniToon Auto Manager Bot

Production-oriented Telegram bot for join-request automation and legitimate
single-bot reactions on managed channels and groups.

## Features

- Per-user, per-channel configuration with ownership checks.
- Public `@username`, private `-100…` chat IDs, and forwarded channel-post setup.
- Auto accept, delayed accept, auto decline, and manual join-request modes.
- Per-channel approval and reaction delays.
- Weighted reaction selection with emoji percentages that must total 100%.
- Message-type targeting for text, photos, videos, documents, and audio.
- Optional welcome messages with safe placeholders.
- Four owner-managed force-subscribe slots with real Telegram membership checks.
- Owner/admin authorization by numeric Telegram IDs.
- MongoDB persistence with a bounded temporary in-memory fallback on outages.
- Async queues, concurrency limits, duplicate event protection, FloodWait handling,
  retries, and graceful shutdown.
- `GET /`, `GET /health`, `GET /ping`, and `GET /status` health endpoints.

The bot uses only Telegram-supported bot operations. It does not create fake
accounts, simulate engagement, or bypass permissions.

## Configuration

Put required values in Replit Secrets, not in GitHub:

| Name | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | yes | BotFather token |
| `API_ID` | yes | Telegram application ID |
| `API_HASH` | yes | Telegram application hash |
| `OWNER_ID` | yes | Numeric owner Telegram user ID |
| `MONGO_URI` or `MONGO_DB` | yes | MongoDB connection URI; `MONGO_URI` takes precedence |
| `PORT` | no | HTTP port, default `8000` |
| `SUPPORT_USERNAME` | no | Support account without `@` |
| `UPDATES_CHANNEL` | no | Optional updates channel/link |
| `MONGO_DATABASE` | no | Database name, default `anitoon_auto_manager` |

No credentials are logged, displayed by the UI, or included in `.env.example`.
Force-subscribe channels are intentionally configured by the owner inside the
bot and are never invented by the application.

## Run

```bash
python main.py
```

Replit deployment uses the same command and respects `PORT` (default `8080`). The `/` endpoint returns `Replit Telegram bot is running`, `/health` returns `OK`, `/ping` returns `pong`, and `/status` returns JSON.

## Telegram permissions

The bot must be an administrator in each managed channel/group. To process join
requests, enable **Invite Users via Link**. Telegram may expose additional
permissions for reactions or posting depending on the chat type; only the
permissions available in Telegram are used.