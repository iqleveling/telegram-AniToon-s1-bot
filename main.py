"""Production entry point for the Auto Manager Telegram bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from contextlib import suppress

from aiohttp import web
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.database import Repository
from bot.keyboards import (
    add_channel, add_channel_choice, back, channel_settings, join_menu, kb, main_menu,
    owner_menu, reaction_menu, target_menu, timing_menu, welcome_menu, permissions_menu,
)
from bot.services import (
    DEFAULT_TARGETS, JOIN_MODES, SUPPORTED_TARGETS, choose_weighted_reaction,
    message_target, normalize_chat_reference, parse_reaction_input,
    render_welcome, targets_match, validate_reaction_set,
)
from config import ANITOON_NETWORK_URL, OWNER_DISPLAY, Settings, load_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("AniToonBot")

STARTED_AT = time.monotonic()


def text_main(owner: bool = False) -> str:
    return (
        "🤖 **AUTO JOIN REQUEST & AUTO REACTION BOT**\n\n"
        "What would you like to do?"
    )


def about_text(settings: Settings) -> str:
    support = (
        f"🆘 Support: @{settings.support_username}\n"
        if settings.support_username else
        "🆘 Support: configure SUPPORT_USERNAME to enable this link.\n"
    )
    updates = (
        f"\n📣 Updates: {settings.updates_channel}"
        if settings.updates_channel else ""
    )
    return (
        "╔════════════════════════════╗\n"
        "     🤖 AUTO MANAGER BOT\n"
        "╚════════════════════════════╝\n\n"
        "⚡ Auto Join Request Manager\n"
        "❤️ Auto Reaction System\n"
        "📢 Multi-Channel Support\n"
        "⏱️ Custom Approval Timing\n"
        "🔒 Secure Configuration\n"
        "🗄️ MongoDB Storage\n"
        "🚀 Fast & Reliable\n\n"
        f"👑 Bot Creator:\n{OWNER_DISPLAY}\n\n"
        "📢 AniToon's Channels Bots and Main Group\n"
        f"{support}{updates}"
    )


def about_markup(settings: Settings) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📢 AniToon's Network", url=ANITOON_NETWORK_URL)],
    ]
    if settings.support_username:
        rows.append([
            InlineKeyboardButton(
                "🆘 Support",
                url=f"https://t.me/{settings.support_username}",
            )
        ])
    else:
        rows.append([InlineKeyboardButton("🆘 Support", callback_data="support")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def help_text() -> str:
    return (
        "❓ **HELP**\n\n"
        "• Add a public channel with @username, or a private chat with its "
        "numeric -100… chat ID.\n"
        "• Add this bot as an administrator and enable **Invite Users via "
        "Link** (needed to manage join requests). Give posting/reaction "
        "permission where Telegram offers it.\n"
        "• Choose Auto Accept, Delayed Accept, Auto Decline, or Manual.\n"
        "• Approval and reaction delays are independent per channel.\n"
        "• Add emoji weights whose total is exactly 100%.\n"
        "• Select which message types receive reactions.\n"
        "• Disable a channel to pause processing without deleting settings.\n"
        "• Remove only deletes this bot's saved configuration.\n"
        "• Force Subscribe is verified with Telegram membership checks; it "
        "never fakes a successful check."
    )


class EventProcessor:
    def __init__(self, client: Client, repository: Repository):
        self.client = client
        self.repository = repository
        self.join_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.reaction_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.semaphore = asyncio.Semaphore(10)
        self.seen: dict[str, float] = {}
        self.workers: list[asyncio.Task] = []
        self.stopping = False

    async def start(self):
        self.workers = [
            asyncio.create_task(self._join_worker(), name="join-worker"),
            asyncio.create_task(self._reaction_worker(), name="reaction-worker"),
        ]

    async def stop(self):
        self.stopping = True
        for worker in self.workers:
            worker.cancel()
        for worker in self.workers:
            with suppress(asyncio.CancelledError):
                await worker

    def _new_event(self, key: str) -> bool:
        now = time.monotonic()
        self.seen = {item: timestamp for item, timestamp in self.seen.items()
                     if now - timestamp < 3600}
        if key in self.seen:
            return False
        self.seen[key] = now
        return True

    async def enqueue_join(self, request):
        key = f"join:{request.chat.id}:{request.from_user.id}"
        if self._new_event(key):
            with suppress(asyncio.QueueFull):
                self.join_queue.put_nowait(request)

    async def enqueue_reaction(self, message):
        key = f"reaction:{message.chat.id}:{message.id}"
        if self._new_event(key):
            with suppress(asyncio.QueueFull):
                self.reaction_queue.put_nowait(message)

    async def _retry(self, operation):
        delay = 1
        for attempt in range(4):
            try:
                async with self.semaphore:
                    return await operation()
            except FloodWait as exc:
                await asyncio.sleep(min(int(exc.value), 300))
            except (asyncio.TimeoutError, OSError):
                if attempt == 3:
                    raise
                await asyncio.sleep(delay)
                delay *= 2

    async def _join_worker(self):
        while not self.stopping:
            request = await self.join_queue.get()
            try:
                config = await self.repository.get_any_channel_for_chat(
                    request.chat.id
                )
                if not config or not config.get("enabled", True):
                    continue
                if not config.get("join_enabled", True):
                    continue
                mode = config.get("mode", "auto")
                if mode == "manual":
                    continue
                delay = int(config.get("approval_delay", 0))
                if mode == "decline":
                    if delay:
                        await asyncio.sleep(delay)
                    await self._retry(
                        lambda: self.client.decline_chat_join_request(
                            request.chat.id, request.from_user.id
                        )
                    )
                else:
                    if mode == "delayed" or delay:
                        await asyncio.sleep(delay)
                    await self._retry(
                        lambda: self.client.approve_chat_join_request(
                            request.chat.id, request.from_user.id
                        )
                    )
                    if config.get("welcome_enabled") and config.get("welcome_message"):
                        welcome = render_welcome(
                            config["welcome_message"], request.from_user,
                            request.chat.title or "the chat",
                        )
                        with suppress(Exception):
                            await self._retry(
                                lambda: self.client.send_message(
                                    request.chat.id, welcome
                                )
                            )
            except Exception:
                log.exception("Join request processing failed safely")
            finally:
                self.join_queue.task_done()

    async def _reaction_worker(self):
        while not self.stopping:
            message = await self.reaction_queue.get()
            try:
                config = await self.repository.get_any_channel_for_chat(
                    message.chat.id
                )
                if not config or not config.get("enabled", True):
                    continue
                # Reactions have their own flag; channel enabled remains separate.
                if not config.get("reaction_enabled", False):
                    continue
                if not targets_match(message, config.get("targets", ["all"])):
                    continue
                reaction = choose_weighted_reaction(config.get("reactions", []))
                if not reaction:
                    continue
                await asyncio.sleep(int(config.get("reaction_delay", 0)))
                await self._retry(
                    lambda: self.client.send_reaction(
                        message.chat.id, message.id, reaction
                    )
                )
            except Exception:
                log.exception("Reaction processing failed safely")
            finally:
                self.reaction_queue.task_done()


class BotApplication:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = Repository(settings.mongo_db, settings.mongo_database)
        self.client = Client(
            "anitoon_auto_manager",
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            bot_token=settings.bot_token,
        )
        self.processor = EventProcessor(self.client, self.repository)
        self.states: dict[int, dict] = {}
        self.http_runner: web.AppRunner | None = None
        self.me = None

    def is_owner(self, user_id: int) -> bool:
        return int(user_id) == self.settings.owner_id

    async def is_admin(self, user_id: int) -> bool:
        return self.is_owner(user_id) or await self.repository.has_admin(user_id)

    async def allowed(self, user_id: int) -> bool:
        if await self.is_admin(user_id):
            return True
        required = await self.repository.active_force_channels()
        if not required:
            return True
        for channel in required:
            try:
                member = await self.client.get_chat_member(
                    channel["channel_id"], user_id
                )
                if getattr(member, "status", "") in {
                    "member", "administrator", "owner"
                }:
                    continue
                return False
            except Exception:
                return False
        return True

    async def force_markup(self):
        rows = []
        for item in await self.repository.active_force_channels():
            label = item.get("title") or f"Channel {item['slot']}"
            link = item.get("link") or item.get("username")
            if link:
                if link.startswith(("http://", "https://", "tg://")):
                    rows.append([InlineKeyboardButton(f"📢 {label}", url=link)])
                else:
                    rows.append([InlineKeyboardButton(
                        f"📢 {label}", callback_data=f"forceinfo:{item['slot']}"
                    )])
        rows.append([InlineKeyboardButton("✅ I Joined - Check Again",
                                          callback_data="forcecheck")])
        return InlineKeyboardMarkup(rows)

    async def guard_message(self, message: Message) -> bool:
        if await self.allowed(message.from_user.id):
            return True
        await message.reply_text(
            "╔══════════════════════════╗\n"
            "      🔒 JOIN REQUIRED\n"
            "╚══════════════════════════╝\n\n"
            "Please join our required channels first.",
            reply_markup=await self.force_markup(),
        )
        return False

    async def guard_callback(self, query: CallbackQuery) -> bool:
        if query.data.startswith(("force", "about", "help", "main")):
            return True
        if await self.allowed(query.from_user.id):
            return True
        await query.answer("Join the required channels first.", show_alert=True)
        await query.message.edit_text(
            "🔒 **JOIN REQUIRED**\n\nPlease join our required channels first.",
            reply_markup=await self.force_markup(),
        )
        return False

    async def make_channel(self, user_id: int, reference) -> tuple[bool, str, dict | None]:
        try:
            chat = await self.client.get_chat(reference)
            bot_member = await self.client.get_chat_member(chat.id, self.me.id)
            status = getattr(bot_member, "status", "")
            privileges = getattr(bot_member, "privileges", None)
            can_invite = bool(getattr(privileges, "can_invite_users", False))
            if status not in {"administrator", "owner"} or (
                status == "administrator" and not can_invite
            ):
                return False, (
                    "❌ I could not access this channel.\n\n"
                    "Please make sure:\n"
                    "✅ The bot is added as an administrator.\n"
                    "✅ Invite Users via Link permission is enabled.\n"
{