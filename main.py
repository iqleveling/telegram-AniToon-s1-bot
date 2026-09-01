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
    add_channel, back, channel_settings, join_menu, kb, main_menu,
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
                    "✅ The username or chat ID is correct."
                ), None
            data = {
                "chat_id": chat.id,
                "title": chat.title or "Unnamed chat",
                "username": f"@{chat.username}" if chat.username else "",
                "link": f"https://t.me/{chat.username}" if chat.username else "",
            }
            await self.repository.save_channel(user_id, data)
            return True, "✅ Channel connected.", data
        except Exception:
            log.exception("Channel validation failed")
            return False, (
                "❌ I could not access this channel.\n\n"
                "Please make sure:\n"
                "✅ The bot is added as an administrator.\n"
                "✅ Required permissions are enabled.\n"
                "✅ The username or chat ID is correct."
            ), None

    async def channel_text(self, user_id: int, chat_id: int) -> str:
        config = await self.repository.get_channel(user_id, chat_id)
        if not config:
            return "❌ This channel is no longer connected."
        label = config.get("username") or config.get("title", str(chat_id))
        mode = {"auto": "🟢 AUTO ACCEPT", "delayed": "⏱️ DELAYED ACCEPT",
                "decline": "🔴 AUTO DECLINE", "manual": "🟡 MANUAL"}.get(
                    config.get("mode"), "🟡 MANUAL"
                )
        return (
            "📢 **CHANNEL SETTINGS**\n\n"
            f"Channel: {label}\n"
            f"Status: {'🟢 ACTIVE' if config.get('enabled', True) else '🔴 DISABLED'}\n"
            f"Join Requests: {'✅ ON' if config.get('join_enabled', True) else '❌ OFF'}\n"
            f"Mode: {mode}\n"
            f"Approval Delay: ⏱️ {config.get('approval_delay', 0)} seconds\n"
            f"Auto Reactions: {'❤️ ON' if config.get('reaction_enabled', False) else '❌ OFF'}\n"
            f"Reaction Delay: ⏱️ {config.get('reaction_delay', 0)} seconds"
        )

    async def reaction_text(self, user_id: int, chat_id: int) -> str:
        config = await self.repository.get_channel(user_id, chat_id) or {}
        reactions = config.get("reactions", [])
        total = sum(int(item.get("percentage", 0)) for item in reactions)
        lines = "\n".join(
            f"{item['emoji']} — {item['percentage']}%" for item in reactions
        ) or "No reactions configured yet."
        return (
            "❤️ **AUTO REACTION SETTINGS**\n\n"
            f"Status: {'✅ ON' if config.get('reaction_enabled', False) else '❌ OFF'}\n\n"
            f"Current Reactions:\n{lines}\n\n"
            f"Total: {total}% {'✅' if total == 100 else '⚠️'}"
        )

    def register_handlers(self):
        app = self.client

        @app.on_message(filters.private & filters.command("start"))
        async def start(_, message):
            await self.repository.upsert_user(
                message.from_user.id, message.from_user.username or ""
            )
            if not await self.guard_message(message):
                return
            await message.reply_text(
                text_main(self.is_owner(message.from_user.id)),
                reply_markup=main_menu(await self.is_admin(message.from_user.id)),
            )

        @app.on_message(filters.private & filters.command("help"))
        async def help_handler(_, message):
            if await self.guard_message(message):
                await message.reply_text(help_text(), reply_markup=back())

        @app.on_message(filters.private & filters.command("about"))
        async def about_handler(_, message):
            await message.reply_text(
                about_text(self.settings),
                reply_markup=about_markup(self.settings),
            )

        @app.on_message(filters.private & filters.command("status"))
        async def status_handler(_, message):
            if not await self.guard_message(message):
                return
            count = await self.repository.count_channels()
            db = "🟢 Connected" if self.repository.connected else "🟡 Temporary memory"
            uptime = int(time.monotonic() - STARTED_AT)
            await message.reply_text(
                f"🤖 Bot: 🟢 Online\n🗄️ Database: {db}\n"
                f"📢 Channels: {count}\n❤️ Auto Reactions: 🟢 Available\n"
                f"⏱️ Uptime: {uptime}s"
            )

        @app.on_chat_join_request()
        async def join_request(_, request):
            await self.processor.enqueue_join(request)

        @app.on_message((filters.channel | filters.group) & ~filters.service)
        async def channel_message(_, message):
            if message_target(message):
                await self.processor.enqueue_reaction(message)

        @app.on_callback_query()
        async def callbacks(_, query):
            await self.handle_callback(query)

        @app.on_message(
            filters.private
            & filters.text
            & ~filters.command(["start", "help", "about", "status"])
        )
        async def text_input(_, message):
            await self.handle_text(message)

    async def handle_text(self, message: Message):
        user_id = message.from_user.id
        state = self.states.get(user_id)
        if not state:
            if await self.guard_message(message):
                await message.reply_text(
                    "Use the menu below.", reply_markup=main_menu(
                        await self.is_admin(user_id)
                    )
                )
            return
        action = state.get("action")
        if action in {"add_channel", "add_id", "add_find"}:
            reference = (
                getattr(message, "forward_from_chat", None).id
                if action == "add_find" and getattr(message, "forward_from_chat", None)
                else normalize_chat_reference(message.text)
            )
            if reference is None:
                await message.reply_text(
                    "Send a valid @username or private -100… chat ID.",
                    reply_markup=back("add"),
                )
                return
            ok, response, data = await self.make_channel(user_id, reference)
            if not ok:
                await message.reply_text(response, reply_markup=back("add"))
                return
            self.states.pop(user_id, None)
            await message.reply_text(
                response + "\n\n" + await self.channel_text(user_id, data["chat_id"]),
                reply_markup=channel_settings(data["chat_id"], True),
            )
            return
        if action in {"approval_custom", "reaction_delay_custom"}:
            try:
                seconds = int(message.text.strip())
                if seconds < 0 or seconds > 86400:
                    raise ValueError
            except ValueError:
                await message.reply_text("Enter a whole number from 0 to 86400.")
                return
            field = "approval_delay" if action == "approval_custom" else "reaction_delay"
            await self.repository.update_channel(user_id, state["chat_id"], {field: seconds})
            self.states.pop(user_id, None)
            await message.reply_text("✅ Delay saved.", reply_markup=channel_settings(
                state["chat_id"], True
            ))
            return
        if action == "reaction_add":
            parsed = parse_reaction_input(message.text)
            if not parsed:
                await message.reply_text("Send one emoji and a whole-number percentage, for example: ❤️ 50")
                return
            emoji, percentage = parsed
            config = await self.repository.get_channel(user_id, state["chat_id"]) or {}
            replace = state.get("replace")
            reactions = [
                item for item in config.get("reactions", [])
                if item["emoji"] not in {emoji, replace}
            ]
            if sum(int(item["percentage"]) for item in reactions) + percentage > 100:
                await message.reply_text("❌ Total percentage cannot exceed 100%.")
                return
            reactions.append({"emoji": emoji, "percentage": percentage})
            await self.repository.update_channel(user_id, state["chat_id"], {"reactions": reactions})
            self.states.pop(user_id, None)
            await message.reply_text(
                await self.reaction_text(user_id, state["chat_id"]),
                reply_markup=reaction_menu(state["chat_id"], config.get("reaction_enabled", False)),
            )
            return
        if action == "welcome_edit":
            value = message.text.strip()
            if not value or len(value) > 1000:
                await message.reply_text("Message must be 1–1000 characters.")
                return
            await self.repository.update_channel(
                user_id, state["chat_id"], {"welcome_message": value}
            )
            self.states.pop(user_id, None)
            await message.reply_text("✅ Welcome message saved.", reply_markup=welcome_menu(
                state["chat_id"], True
            ))
            return
        if action == "admin_add":
            try:
                target_id = int(message.text.strip())
                if target_id <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text("Send a numeric Telegram user ID.")
                return
            await self.repository.save_admin(target_id, ["manage"])
            self.states.pop(user_id, None)
            await message.reply_text("✅ Authorized admin saved.", reply_markup=owner_menu())
            return
        if action == "force_slot":
            reference = normalize_chat_reference(message.text)
            if reference is None:
                await message.reply_text("Send @username, a t.me link, or a -100… chat ID.")
                return
            try:
                chat = await self.client.get_chat(reference)
                link = (
                    f"https://t.me/{chat.username}" if chat.username
                    else str(reference)
                )
                await self.repository.save_force_channel(
                    state["slot"],
                    {
                        "channel_id": chat.id,
                        "title": chat.title or f"Channel {state['slot']}",
                        "username": f"@{chat.username}" if chat.username else "",
                        "link": link,
                        "enabled": True,
                        "private": not bool(chat.username),
                    },
                )
                self.states.pop(user_id, None)
                await message.reply_text("✅ Force-subscribe channel saved.",
                                         reply_markup=owner_menu())
            except Exception:
                await message.reply_text("❌ Telegram could not access that channel. Check the bot's membership and identifier.")

    async def handle_callback(self, query: CallbackQuery):
        await query.answer()
        if not await self.guard_callback(query):
            return
        user_id = query.from_user.id
        data = query.data
        if data.startswith("url:"):
            await query.answer("Opening link…")
            return
        if data == "support":
            text = (
                f"🆘 Support: @{self.settings.support_username}"
                if self.settings.support_username else
                "🆘 Support is not configured yet. Ask the owner to set SUPPORT_USERNAME."
            )
            await query.message.edit_text(text, reply_markup=back("about"))
        elif data == "main":
            await query.message.edit_text(
                text_main(self.is_owner(user_id)),
                reply_markup=main_menu(await self.is_admin(user_id)),
            )
        elif data == "about":
            await query.message.edit_text(
                about_text(self.settings), reply_markup=about_markup(self.settings)
            )
        elif data == "help":
            await query.message.edit_text(help_text(), reply_markup=back())
        elif data == "add":
            await query.message.edit_text(
                "📢 **ADD CHANNEL**\n\n"
                "The bot can manage a channel/group only after it has been "
                "added as an administrator with the required permissions.",
                reply_markup=add_channel(),
            )
        elif data in {"add_channel", "add_id", "add_find"}:
            self.states[user_id] = {"action": data}
            prompt = (
                "Forward a channel post here. Telegram does not expose every "
                "private channel managed by a user automatically."
                if data == "add_find" else
                "Send the public @username or private -100… chat ID."
            )
            await query.message.edit_text(prompt, reply_markup=back("add"))
        elif data == "add_how":
            await query.message.edit_text(
                "❓ **HOW TO ADD THE BOT**\n\n"
                "Open the channel/group → Administrators → Add Admin → "
                "select this bot. Enable Invite Users via Link. For auto "
                "reactions, also enable the reaction/post permissions Telegram "
                "offers. Then return and add the @username or -100… ID.",
                reply_markup=back("add"),
            )
        elif data == "channels":
            channels = await self.repository.get_channels(user_id)
            rows = [
                [(
                    f"{'🟢' if channel.get('enabled', True) else '🔴'} "
                    f"{channel.get('username') or channel.get('title')}",
                    f"channel:{channel['chat_id']}",
                )]
                for channel in channels
            ]
            rows.append([("➕ Add Channel", "add"), ("⬅️ Back", "main")])
            await query.message.edit_text(
                "📢 **MY CHANNELS**\n\n" + (
                    "Select a channel:" if channels else "No channels connected yet."
                ), reply_markup=kb(rows))
        elif data == "reaction_channels":
            channels = await self.repository.get_channels(user_id)
            rows = [
                [(f"📢 {c.get('username') or c.get('title')}",
                  f"reaction:{c['chat_id']}")]
                for c in channels
            ]
            rows.append([("⬅️ Back", "main")])
            await query.message.edit_text(
                "❤️ **AUTO REACTIONS**\n\nSelect a channel to configure:" if channels else "❤️ **AUTO REACTIONS**\n\nNo channels connected yet.",
                reply_markup=kb(rows),
            )
        elif data.startswith("channel:"):
            chat_id = int(data.split(":"[1]))
