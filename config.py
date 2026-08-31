"""Environment-backed configuration for the Auto Manager bot."""

from __future__ import annotations

import os
from dataclasses import dataclass


OWNER_DISPLAY = "@MonkeyDLuffy_Prince"
ANITOON_NETWORK_URL = "https://t.me/Anitoon_edit/33"


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int
    api_hash: str
    owner_id: int
    mongo_db: str
    port: int = 8000
    support_username: str = ""
    updates_channel: str = ""
    mongo_database: str = "anitoon_auto_manager"

    @property
    def secret_status(self) -> dict[str, bool]:
        return {
            "BOT_TOKEN": bool(self.bot_token),
            "API_ID": self.api_id > 0,
            "API_HASH": bool(self.api_hash),
            "OWNER_ID": self.owner_id > 0,
            "MONGO_DB": bool(self.mongo_db),
        }


def load_settings(strict: bool = True) -> Settings:
    """Read configuration without ever logging or displaying secret values."""
    raw = {
        "bot_token": os.getenv("BOT_TOKEN", ""),
        "api_id": os.getenv("API_ID", "0"),
        "api_hash": os.getenv("API_HASH", ""),
        "owner_id": os.getenv("OWNER_ID", "0"),
        "mongo_db": os.getenv("MONGO_DB", ""),
    }
    if strict:
        missing = [
            name
            for name, value in (
                ("BOT_TOKEN", raw["bot_token"]),
                ("API_ID", raw["api_id"]),
                ("API_HASH", raw["api_hash"]),
                ("OWNER_ID", raw["owner_id"]),
                ("MONGO_DB", raw["mongo_db"]),
            )
            if not value or value == "0"
        ]
        if missing:
            raise RuntimeError(
                "Missing required secrets: " + ", ".join(missing)
            )
    try:
        api_id = int(raw["api_id"] or 0)
        owner_id = int(raw["owner_id"] or 0)
        port = int(os.getenv("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("API_ID, OWNER_ID, and PORT must be integers") from exc
    return Settings(
        bot_token=raw["bot_token"],
        api_id=api_id,
        api_hash=raw["api_hash"],
        owner_id=owner_id,
        mongo_db=raw["mongo_db"],
        port=port,
        support_username=os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@"),
        updates_channel=os.getenv("UPDATES_CHANNEL", "").strip(),
        mongo_database=os.getenv("MONGO_DATABASE", "anitoon_auto_manager"),
    )