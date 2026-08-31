"""MongoDB persistence with a bounded in-memory fallback for outages."""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime, timezone
from typing import Any

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:  # Allows pure unit tests before dependencies are installed.
    AsyncIOMotorClient = None

log = logging.getLogger("AniToonBot.database")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Repository:
    collections = (
        "users",
        "channels",
        "channel_settings",
        "join_request_settings",
        "reaction_settings",
        "permissions_settings",
        "admins",
        "force_subscribe",
    )

    def __init__(self, uri: str, database_name: str):
        self.uri = uri
        self.database_name = database_name
        self.client = None
        self.db = None
        self.connected = False
        self._memory: dict[str, dict[Any, dict]] = {
            collection: {} for collection in self.collections
        }
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(owner_user_id: int, chat_id: int) -> tuple[int, int]:
        return owner_user_id, chat_id

    async def connect(self) -> bool:
        if not self.uri or AsyncIOMotorClient is None:
            self.connected = False
            return False
        try:
            self.client = AsyncIOMotorClient(
                self.uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000
            )
            await self.client.admin.command("ping")
            self.db = self.client[self.database_name]
            await self._create_indexes()
            self.connected = True
            await self._flush_memory()
            log.info("MongoDB connected")
            return True
        except Exception:
            self.connected = False
            log.exception("MongoDB unavailable; using temporary memory fallback")
            return False

    async def _flush_memory(self) -> None:
        """Best-effort persistence of writes made during a short outage."""
        if not self.connected:
            return
        async with self._lock:
            pending = {
                collection: list(documents.values())
                for collection, documents in self._memory.items()
                if documents
            }
        for collection, documents in pending.items():
            for document in documents:
                if "owner_user_id" in document and "chat_id" in document:
                    query = {
                        "owner_user_id": document["owner_user_id"],
                        "chat_id": document["chat_id"],
                    }
                elif "user_id" in document:
                    query = {"user_id": document["user_id"]}
                elif "slot" in document:
                    query = {"slot": document["slot"]}
                else:
                    continue
                await self.db[collection].update_one(
                    query, {"$set": document}, upsert=True
                )

    async def reconnect(self) -> bool:
        if self.connected:
            return True
        return await self.connect()

    async def _create_indexes(self) -> None:
        for collection in self.collections:
            await self.db[collection].create_index("owner_user_id")
            await self.db[collection].create_index("chat_id")
            await self.db[collection].create_index("username")
        await self.db["channels"].create_index(
            [("owner_user_id", 1), ("chat_id", 1)], unique=True
        )
        await self.db["reaction_settings"].create_index(
            [("owner_user_id", 1), ("chat_id", 1)], unique=True
        )
        await self.db["permissions_settings"].create_index(
            [("owner_user_id", 1), ("chat_id", 1)], unique=True
        )

    async def close(self) -> None:
        if self.client:
            self.client.close()
        self.connected = False

    async def _find_one(self, collection: str, query: dict) -> dict | None:
        if self.connected:
            return await self.db[collection].find_one(query, {"_id": 0})
        async with self._lock:
            for document in self._memory[collection].values():
                if all(document.get(k) == v for k, v in query.items()):
                    return copy.deepcopy(document)
        return None

    async def _find_many(self, collection: str, query: dict) -> list[dict]:
        if self.connected:
            return [
                item async for item in self.db[collection].find(query, {"_id": 0})
            ]
        async with self._lock:
            return [
                copy.deepcopy(document)
                for document in self._memory[collection].values()
                if all(document.get(k) == v for k, v in query.items())
            ]

    async def _upsert(self, collection: str, query: dict, values: dict) -> dict:
        document = {**query, **values}
        if self.connected:
            await self.db[collection].update_one(
                query, {"$set": document}, upsert=True
            )
            return document
        key = tuple(query.items())
        async with self._lock:
            current = self._memory[collection].get(key, {})
            self._memory[collection][key] = {**current, **document}
            return copy.deepcopy(self._memory[collection][key])

    async def _delete(self, collection: str, query: dict) -> None:
        if self.connected:
            await self.db[collection].delete_many(query)
            return
        async with self._lock:
            self._memory[collection] = {
                key: document
                for key, document in self._memory[collection].items()
                if not all(document.get(k) == v for k, v in query.items())
            }

    async def upsert_user(self, user_id: int, username: str = "") -> None:
        await self._upsert(
            "users",
            {"user_id": user_id},
            {"username": username, "updated_at": utc_now()},
        )

    async def save_channel(self, owner_user_id: int, channel: dict) -> dict:
        chat_id = int(channel["chat_id"])
        query = {"owner_user_id": owner_user_id, "chat_id": chat_id}
        stored = await self._upsert(
            "channels",
            query,
            {
                "title": channel.get("title", "Unnamed chat"),
                "username": channel.get("username", ""),
                "link": channel.get("link", ""),
                "enabled": channel.get("enabled", True),
                "updated_at": utc_now(),
            },
        )
        await self._upsert(
            "channel_settings",
            query,
            {
                "welcome_enabled": False,
                "welcome_message": "Welcome {first_name}!",
            },
        )
        await self._upsert(
            "join_request_settings",
            query,
            {
                "join_enabled": True,
                "mode": "auto",
                "approval_delay": 0,
            },
        )
        await self._upsert(
            "reaction_settings",
            query,
            {
                "reaction_enabled": False,
                "reactions": [],
                "reaction_delay": 0,
                "targets": ["all"],
            },
        )
        await self._upsert(
            "permissions_settings",
            query,
            {
                "change_info": True,
                "manage_messages": True,
                "manage_stories": True,
                "direct_messages": True,
                "invite_users": True,
                "live_streams": True,
                "add_admins": True,
                "ban_users": True,
            },
        )
        return stored

    async def get_channel(self, owner_user_id: int, chat_id: int) -> dict | None:
        query = {"owner_user_id": owner_user_id, "chat_id": int(chat_id)}
        channel = await self._find_one("channels", query)
        if not channel:
            return None
        settings = await self._find_one("channel_settings", query) or {}
        join = await self._find_one("join_request_settings", query) or {}
        reactions = await self._find_one("reaction_settings", query) or {}
        permissions = await self._find_one("permissions_settings", query) or {}
        return {**channel, **settings, **join, **reactions, **permissions}

    async def get_channels(self, owner_user_id: int) -> list[dict]:
        channels = await self._find_many(
            "channels", {"owner_user_id": owner_user_id}
        )
        result = []
        for channel in channels:
            item = await self.get_channel(owner_user_id, channel["chat_id"])
            if item:
                result.append(item)
        return sorted(result, key=lambda item: item.get("title", "").lower())

    async def get_any_channel_for_chat(self, chat_id: int) -> dict | None:
        channels = await self._find_many("channels", {"chat_id": int(chat_id)})
        if not channels:
            return None
        return await self.get_channel(
            int(channels[0]["owner_user_id"]), int(chat_id)
        )

    async def update_channel(
        self, owner_user_id: int, chat_id: int, values: dict
    ) -> dict | None:
        query = {"owner_user_id": owner_user_id, "chat_id": int(chat_id)}
        if not await self._find_one("channels", query):
            return None
        collection = "channels"
        if any(key in values for key in ("welcome_enabled", "welcome_message")):
            await self._upsert("channel_settings", query, values)
            values = {
                key: value
                for key, value in values.items()
                if key not in ("welcome_enabled", "welcome_message")
            }
        if any(key in values for key in ("mode", "approval_delay")):
            await self._upsert("join_request_settings", query, values)
            values = {
                key: value
                for key, value in values.items()
                if key not in ("mode", "approval_delay")
            }
        if any(key in values for key in (
            "reaction_enabled", "reactions", "reaction_delay", "targets"
        )):
            await self._upsert("reaction_settings", query, values)
            values = {
                key: value
                for key, value in values.items()
                if key not in (
                    "reaction_enabled", "reactions", "reaction_delay", "targets"
                )
            }
        permission_keys = {
            "change_info", "manage_messages", "manage_stories",
            "direct_messages", "invite_users", "live_streams",
            "add_admins", "ban_users"
        }
        if any(key in values for key in permission_keys):
            await self._upsert("permissions_settings", query, values)
            values = {
                key: value
                for key, value in values.items()
                if key not in permission_keys
            }
        if values:
            await self._upsert(collection, query, values)
        return await self.get_channel(owner_user_id, chat_id)

    async def remove_channel(self, owner_user_id: int, chat_id: int) -> None:
        query = {"owner_user_id": owner_user_id, "chat_id": int(chat_id)}
        for collection in (
            "channels",
            "channel_settings",
            "join_request_settings",
            "reaction_settings",
            "permissions_settings",
        ):
            await self._delete(collection, query)

    async def count_channels(self) -> int:
        if self.connected:
            return await self.db["channels"].count_documents({})
        async with self._lock:
            return len(self._memory["channels"])

    async def has_admin(self, user_id: int) -> bool:
        return bool(await self._find_one("admins", {"user_id": int(user_id)}))

    async def save_admin(self, user_id: int, permissions: list[str]) -> None:
        await self._upsert(
            "admins",
            {"user_id": int(user_id)},
            {"permissions": permissions, "updated_at": utc_now()},
        )

    async def remove_admin(self, user_id: int) -> None:
        await self._delete("admins", {"user_id": int(user_id)})

    async def list_admins(self) -> list[dict]:
        return await self._find_many("admins", {})

    async def list_force_subscribe(self) -> list[dict]:
        return sorted(
            await self._find_many("force_subscribe", {}),
            key=lambda item: item.get("slot", 0),
        )

    async def save_force_channel(self, slot: int, values: dict) -> None:
        await self._upsert("force_subscribe", {"slot": int(slot)}, values)

    async def remove_force_channel(self, slot: int) -> None:
        await self._delete("force_subscribe", {"slot": int(slot)})

    async def active_force_channels(self) -> list[dict]:
        return [
            item for item in await self.list_force_subscribe()
            if item.get("enabled", True)
        ]
