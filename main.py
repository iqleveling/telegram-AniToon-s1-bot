"""Production entry point for the AniToon Telegram manager bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from contextlib import suppress

from aiohttp import web
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database import Repository
from bot.keyboards import (
    add_channel, add_channel_choice, back, channel_settings, join_menu, kb,
    main_menu, owner_menu, reaction_menu, target_menu, timing_menu, welcome_menu,
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
    return "🤖 **AUTO JOIN REQUEST & AUTO REACTION BOT**\n\nWhat would you like to do?"


def about_text(settings: Settings) -> str:
    support = (
        f"🆘 Support: @{settings.support_username}\n"
        if settings.support_username
        else "🆘 Support: configure SUPPORT_USERNAME to enable this link.\n"
    )
    updates = f"\n📣 Updates: {settings.updates_channel}" if settings.updates_channel else ""
    return (
        "╔════════════════════════════╗\n"
        "     🤖 AUTO MANAGER BOT\n"
        "╚════════════════════════════╝\n\n"
        "⚡ Auto Join Request Manager\n❤️ Auto Reaction System\n"
        "📢 Multi-Channel Support\n⏱️ Custom Approval Timing\n"
        "🔒 Secure Configuration\n🗄️ MongoDB Storage\n🚀 Fast & Reliable\n\n"
        f"👑 Bot Creator:\n{OWNER_DISPLAY}\n\n"
        "📢 AniToon's Channels Bots and Main Group\n"
        f"{support}{updates}"
    )


def about_markup(settings: Settings) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📢 AniToon's Network", url=ANITOON_NETWORK_URL)]]
    if settings.support_username:
        rows.append([InlineKeyboardButton("🆘 Support", url=f"https://t.me/{settings.support_username}")])
    else:
        rows.append([InlineKeyboardButton("🆘 Support", callback_data="support")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def help_text() -> str:
    return (
        "❓ **HELP**\n\n"
        "• Add a public channel with @username, a private chat with its numeric -100… ID, or forward a channel post.\n"
        "• Add this bot as an administrator and enable **Invite Users via Link** for join requests.\n"
        "• Choose Auto Accept, Delayed Accept, Auto Decline, or Manual.\n"
        "• Approval and reaction delays are independent per channel.\n"
        "• Add emoji weights whose total is exactly 100%.\n"
        "• Select which message types receive reactions.\n"
        "• Disable a channel to pause processing without deleting its settings.\n"
        "• Force Subscribe is verified with Telegram membership checks; it never fakes a successful check."
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
        self.stopping = False
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
        self.workers.clear()

    def _new_event(self, key: str) -> bool:
        now = time.monotonic()
        self.seen = {item: stamp for item, stamp in self.seen.items() if now - stamp < 3600}
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
                config = await self.repository.get_any_channel_for_chat(request.chat.id)
                if not config or not config.get("enabled", True) or not config.get("join_enabled", True):
                    continue
                mode = config.get("mode", "auto")
                if mode == "manual":
                    continue
                delay = int(config.get("approval_delay", 0))
                if mode == "decline":
                    if delay:
                        await asyncio.sleep(delay)
                    await self._retry(lambda: self.client.decline_chat_join_request(request.chat.id, request.from_user.id))
                    continue
                if mode == "delayed" or delay:
                    await asyncio.sleep(delay)
                await self._retry(lambda: self.client.approve_chat_join_request(request.chat.id, request.from_user.id))
                if config.get("welcome_enabled") and config.get("welcome_message"):
                    welcome = render_welcome(config["welcome_message"], request.from_user, request.chat.title or "the chat")
                    with suppress(Exception):
                        await self._retry(lambda: self.client.send_message(request.chat.id, welcome))
            except Exception:
                log.exception("Join request processing failed safely")
            finally:
                self.join_queue.task_done()

    async def _reaction_worker(self):
        while not self.stopping:
            message = await self.reaction_queue.get()
            try:
                config = await self.repository.get_any_channel_for_chat(message.chat.id)
                if not config or not config.get("enabled", True) or not config.get("reaction_enabled", False):
                    continue
                if not targets_match(message, config.get("targets", ["all"])):
                    continue
                reaction = choose_weighted_reaction(config.get("reactions", []))
                if not reaction:
                    continue
                await asyncio.sleep(max(0, int(config.get("reaction_delay", 0))))
                await self._retry(lambda: self.client.send_reaction(message.chat.id, message.id, reaction))
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
        self.stopping = False
        self.bot_status = "starting"
        self.db_task: asyncio.Task | None = None
        self.http_site: web.TCPSite | None = None

    def is_owner(self, user_id: int) -> bool:
        return int(user_id) == self.settings.owner_id

    async def is_admin(self, user_id: int) -> bool:
        return self.is_owner(user_id) or await self.repository.has_admin(user_id)

    async def allowed(self, user_id: int) -> bool:
        if await self.is_admin(user_id):
            return True
        for channel in await self.repository.active_force_channels():
            try:
                member = await self.client.get_chat_member(channel["channel_id"], user_id)
                if getattr(member, "status", "") in {"member", "administrator", "owner"}:
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
                if not link.startswith(("http://", "https://", "tg://")) and link.startswith("@"):
                    link = f"https://t.me/{link[1:]}"
                rows.append([InlineKeyboardButton(f"📢 {label}", url=link)])
        rows.append([InlineKeyboardButton("✅ I Joined - Check Again", callback_data="forcecheck")])
        return InlineKeyboardMarkup(rows)

    async def guard_message(self, message: Message) -> bool:
        if await self.allowed(message.from_user.id):
            return True
        await message.reply_text(
            "╔══════════════════════════╗\n      🔒 JOIN REQUIRED\n╚══════════════════════════╝\n\nYou need to join the required channels to use this bot.",
            reply_markup=await self.force_markup(),
        )
        return False

    async def guard_callback(self, query: CallbackQuery) -> bool:
        data = query.data or ""
        if data.startswith(("force", "about", "help", "main", "support")):
            return True
        if await self.allowed(query.from_user.id):
            return True
        await query.answer("Join the required channels first.", show_alert=True)
        if query.message:
            await query.message.edit_text("🔒 **JOIN REQUIRED**\n\nPlease join the required channels first.", reply_markup=await self.force_markup())
        return False

    async def make_channel(self, user_id: int, reference) -> tuple[bool, str, dict | None]:
        try:
            chat = await self.client.get_chat(reference)
            bot_member = await self.client.get_chat_member(chat.id, self.me.id)
            status = getattr(bot_member, "status", "")
            privileges = getattr(bot_member, "privileges", None)
            can_invite = status == "owner" or bool(getattr(privileges, "can_invite_users", False))
            if status not in {"administrator", "owner"} or not can_invite:
                return False, "❌ The bot must be an administrator with **Invite Users via Link** permission.", None
            username = getattr(chat, "username", "") or ""
            saved = await self.repository.save_channel(user_id, {
                "chat_id": int(chat.id),
                "title": getattr(chat, "title", None) or getattr(chat, "first_name", None) or "Unnamed chat",
                "username": f"@{username}" if username and not username.startswith("@") else username,
                "link": f"https://t.me/{username}" if username else "",
            })
            return True, f"✅ **{saved['title']}** was added successfully.", saved
        except Exception as exc:
            log.info("Channel setup failed for user %s: %s", user_id, exc)
            return False, "❌ I could not access that chat. Check the username/ID and make sure I am an administrator.", None

    async def channel_for(self, user_id: int, chat_id: int):
        return await self.repository.get_channel(user_id, int(chat_id))

    async def edit(self, query: CallbackQuery, text: str, markup=None):
        if not query.message:
            return
        try:
            await query.message.edit_text(text, reply_markup=markup)
        except Exception:
            with suppress(Exception):
                await query.message.reply_text(text, reply_markup=markup)

    async def show_main(self, target, user_id: int):
        markup = main_menu(self.is_owner(user_id))
        if isinstance(target, CallbackQuery):
            await self.edit(target, text_main(self.is_owner(user_id)), markup)
        else:
            await target.reply_text(text_main(self.is_owner(user_id)), reply_markup=markup)

    async def show_channels(self, query: CallbackQuery, user_id: int):
        channels = await self.repository.get_channels(user_id)
        if not channels:
            await self.edit(query, "📢 **MY CHANNELS**\n\nNo channels configured yet.", add_channel())
            return
        rows = [[(f"{'🟢' if item.get('enabled', True) else '🔴'} {item.get('title', 'Unnamed')}", f"channel:{item['chat_id']}")] for item in channels]
        rows.append([("➕ Add Channel", "add"), ("⬅️ Back", "main")])
        await self.edit(query, "📢 **MY CHANNELS**\n\nChoose a channel to manage:", kb(rows))

    async def show_channel(self, query: CallbackQuery, user_id: int, chat_id: int):
        item = await self.channel_for(user_id, chat_id)
        if not item:
            await query.answer("Channel not found.", show_alert=True)
            return
        reactions = item.get("reactions", [])
        reaction_state = "🟢 enabled" if item.get("reaction_enabled") else "🔴 disabled"
        text = (
            f"📢 **{item.get('title', 'Unnamed')}**\n\n"
            f"Chat ID: {item['chat_id']}\nStatus: {'🟢 enabled' if item.get('enabled', True) else '🔴 disabled'}\n"
            f"Join requests: {item.get('mode', 'auto').upper()} ({item.get('approval_delay', 0)}s)\n"
            f"Reactions: {reaction_state} ({len(reactions)} emoji)\n"
            f"Welcome: {'🟢 enabled' if item.get('welcome_enabled') else '🔴 disabled'}"
        )
        await self.edit(query, text, channel_settings(chat_id, item.get("enabled", True)))

    async def show_reaction(self, query: CallbackQuery, user_id: int, chat_id: int):
        item = await self.channel_for(user_id, chat_id)
        if not item:
            await query.answer("Channel not found.", show_alert=True)
            return
        reactions = item.get("reactions", [])
        lines = [f"{x.get('emoji')} — {x.get('percentage')}%" for x in reactions] or ["No emoji configured."]
        _, status = validate_reaction_set(reactions)
        text = "❤️ **AUTO REACTIONS**\n\n" + "\n".join(lines) + f"\n\nTotal: {status}\nState: {'🟢 enabled' if item.get('reaction_enabled') else '🔴 disabled'}"
        await self.edit(query, text, reaction_menu(chat_id, item.get("reaction_enabled", False)))

    async def show_welcome(self, query: CallbackQuery, user_id: int, chat_id: int):
        item = await self.channel_for(user_id, chat_id)
        if not item:
            await query.answer("Channel not found.", show_alert=True)
            return
        template = item.get("welcome_message", "Welcome {first_name}!")
        text = (
            "💬 **WELCOME MESSAGE**\n\n"
            f"State: {'🟢 enabled' if item.get('welcome_enabled') else '🔴 disabled'}\n\n"
            f"{template}\n\n"
            "Placeholders: {first_name}, {last_name}, {username}, {user_id}, {chat_title}"
        )
        await self.edit(query, text, welcome_menu(chat_id, item.get("welcome_enabled", False)))

    async def show_targets(self, query: CallbackQuery, user_id: int, chat_id: int):
        item = await self.channel_for(user_id, chat_id)
        if not item:
            await query.answer("Channel not found.", show_alert=True)
            return
        await self.edit(query, "🎯 **REACTION TARGET TYPES**\n\nSelect the message types that should receive reactions.", target_menu(chat_id, item.get("targets", ["all"])))

    async def owner_page(self, query: CallbackQuery, title: str, body: str, rows=None):
        rows = rows or []
        rows.append([("⬅️ Back", "owner")])
        await self.edit(query, f"👑 **{title}**\n\n{body}", kb(rows))

    async def register_handlers(self):
        @self.client.on_message(filters.private & filters.command("start"))
        async def start_handler(_, message: Message):
            await self.repository.upsert_user(message.from_user.id, message.from_user.username or "")
            if await self.guard_message(message):
                await self.show_main(message, message.from_user.id)

        @self.client.on_message(filters.private & filters.command(["help", "commands"]))
        async def help_handler(_, message: Message):
            if await self.guard_message(message):
                await message.reply_text(help_text(), reply_markup=back())

        @self.client.on_message(filters.private & filters.command("cancel"))
        async def cancel_handler(_, message: Message):
            self.states.pop(message.from_user.id, None)
            await message.reply_text("Cancelled.", reply_markup=main_menu(self.is_owner(message.from_user.id)))

        @self.client.on_message(filters.private & ~filters.command(["start", "help", "commands", "cancel"]))
        async def private_text_handler(_, message: Message):
            if not await self.guard_message(message):
                return
            await self.handle_text(message)

        @self.client.on_chat_join_request()
        async def join_request_handler(_, request):
            await self.processor.enqueue_join(request)

        @self.client.on_message(filters.incoming & (filters.group | filters.channel) & ~filters.service)
        async def channel_message_handler(_, message: Message):
            await self.processor.enqueue_reaction(message)

        @self.client.on_callback_query()
        async def callback_handler(_, query: CallbackQuery):
            await self.handle_callback(query)

    async def handle_text(self, message: Message):
        user_id = message.from_user.id
        state = self.states.get(user_id)
        if not state:
            await message.reply_text("Use the buttons below to configure the bot.", reply_markup=main_menu(self.is_owner(user_id)))
            return
        action = state.get("action")
        text = (message.text or "").strip()
        if action in {"add_channel", "add_id"}:
            reference = getattr(message, "forward_from_chat", None) or text
            if hasattr(reference, "id"):
                reference = reference.id
            normalized = reference if isinstance(reference, int) else normalize_chat_reference(str(reference))
            if normalized is None:
                await message.reply_text("❌ Invalid chat reference. Send @username, a -100… ID, or forward a channel post.")
                return
            ok, result, _ = await self.make_channel(user_id, normalized)
            self.states.pop(user_id, None)
            await message.reply_text(result, reply_markup=main_menu(self.is_owner(user_id)) if ok else add_channel())
            return
        if action == "welcome_edit":
            if not text or len(text) > 4000:
                await message.reply_text("Send a message up to 4000 characters, or /cancel.")
                return
            await self.repository.update_channel(user_id, state["chat_id"], {"welcome_message": text})
            self.states.pop(user_id, None)
            await message.reply_text("✅ Welcome message saved.", reply_markup=main_menu(self.is_owner(user_id)))
            return
        if action in {"reaction_add", "reaction_edit"}:
            parsed = parse_reaction_input(text)
            if not parsed:
                await message.reply_text("Use exactly: emoji percentage\nExample: ❤️ 50")
                return
            emoji, percentage = parsed
            item = await self.channel_for(user_id, state["chat_id"])
            reactions = list(item.get("reactions", [])) if item else []
            old = next((x for x in reactions if x.get("emoji") == emoji), None)
            if old:
                old["percentage"] = percentage
            else:
                reactions.append({"emoji": emoji, "percentage": percentage})
            valid, status = validate_reaction_set(reactions)
            if not valid and "exceed" in status:
                await message.reply_text(f"❌ {status}")
                return
            await self.repository.update_channel(user_id, state["chat_id"], {"reactions": reactions})
            self.states.pop(user_id, None)
            await message.reply_text(f"✅ Emoji saved. {status}", reply_markup=main_menu(self.is_owner(user_id)))
            return
        if action == "reaction_remove":
            item = await self.channel_for(user_id, state["chat_id"])
            reactions = [x for x in (item or {}).get("reactions", []) if x.get("emoji") != text]
            await self.repository.update_channel(user_id, state["chat_id"], {"reactions": reactions, "reaction_enabled": False if not reactions else (item or {}).get("reaction_enabled", False)})
            self.states.pop(user_id, None)
            await message.reply_text("✅ Emoji removed.", reply_markup=main_menu(self.is_owner(user_id)))
            return
        if action in {"approval_delay", "reaction_delay"}:
            try:
                value = int(text)
            except ValueError:
                value = -1
            allowed = {0, 5, 10, 30, 60, 300} if action == "approval_delay" else {0, 2, 5, 10, 30}
            if value not in allowed:
                await message.reply_text(f"Choose one of: {', '.join(map(str, sorted(allowed)))} seconds.")
                return
            key = "approval_delay" if action == "approval_delay" else "reaction_delay"
            await self.repository.update_channel(user_id, state["chat_id"], {key: value})
            self.states.pop(user_id, None)
            await message.reply_text("✅ Delay saved.", reply_markup=main_menu(self.is_owner(user_id)))
            return
        if action == "admin_add":
            try:
                admin_id = int(text)
                if admin_id <= 0:
                    raise ValueError
            except ValueError:
                await message.reply_text("Send a numeric Telegram user ID, or /cancel.")
                return
            await self.repository.save_admin(admin_id, [])
            self.states.pop(user_id, None)
            await message.reply_text("✅ Admin added.", reply_markup=owner_menu())
            return
        if action == "admin_remove":
            try:
                admin_id = int(text)
            except ValueError:
                await message.reply_text("Send the numeric Telegram user ID, or /cancel.")
                return
            await self.repository.remove_admin(admin_id)
            self.states.pop(user_id, None)
            await message.reply_text("✅ Admin removed.", reply_markup=owner_menu())
            return
        if action == "force_add":
            normalized = normalize_chat_reference(text)
            if normalized is None:
                await message.reply_text("Send @username or a -100… chat ID for this slot, or /cancel.")
                return
            try:
                chat = await self.client.get_chat(normalized)
                member = await self.client.get_chat_member(chat.id, self.me.id)
                privileges = getattr(member, "privileges", None)
                if getattr(member, "status", "") not in {"administrator", "owner"}:
                    raise ValueError("not admin")
                if getattr(member, "status", "") != "owner" and not getattr(privileges, "can_invite_users", False):
                    raise ValueError("invite permission missing")
                username = getattr(chat, "username", "") or ""
                await self.repository.save_force_channel(state["slot"], {"channel_id": int(chat.id), "title": getattr(chat, "title", "Channel"), "username": f"@{username}" if username else "", "link": f"https://t.me/{username}" if username else ""})
                self.states.pop(user_id, None)
                await message.reply_text("✅ Force-subscribe channel saved.", reply_markup=owner_menu())
            except Exception:
                await message.reply_text("❌ I could not verify that channel. Make sure the bot is an administrator with invite permission.")
                return
        if action == "broadcast":
            await message.reply_text("Broadcast is intentionally disabled until a recipient policy is configured safely.", reply_markup=owner_menu())
            self.states.pop(user_id, None)
            return

    async def handle_callback(self, query: CallbackQuery):
        data = query.data or ""
        user_id = query.from_user.id
        await query.answer()
        if not await self.guard_callback(query):
            return
        if data == "main":
            await self.show_main(query, user_id); return
        if data == "about":
            await self.edit(query, about_text(self.settings), about_markup(self.settings)); return
        if data in {"help", "support"}:
            await self.edit(query, help_text() if data == "help" else "🆘 Please use the configured support link.", back()); return
        if data == "add":
            await self.edit(query, "➕ **ADD CHANNEL OR CHAT**\n\nChoose how you want to add a managed chat.", add_channel()); return
        if data == "add_channel":
            self.states[user_id] = {"action": "add_channel"}
            await self.edit(query, "📢 Send @username, a -100… chat ID, or forward a channel post.\n\nUse /cancel to stop.", back("add")); return
        if data == "add_id":
            self.states[user_id] = {"action": "add_id"}
            await self.edit(query, "🔢 Send the numeric -100… chat ID.\n\nUse /cancel to stop.", back("add")); return
        if data in {"add_to_channel", "add_to_group"}:
            self.states[user_id] = {"action": "add_channel"}
            kind = "channel" if data.endswith("channel") else "group"
            await self.edit(query, f"Send the {kind}'s @username, -100… ID, or forward a post from it.\n\nUse /cancel to stop.", back("add")); return
        if data == "add_how":
            await self.edit(query, "📚 Add me to the chat, promote me to administrator, enable Invite Users via Link, then send its @username or forward one of its posts here.", back("add")); return
        if data == "channels":
            await self.show_channels(query, user_id); return
        if data == "reaction_channels":
            channels = await self.repository.get_channels(user_id)
            rows = [[(f"❤️ {x.get('title', 'Unnamed')}", f"reaction:{x['chat_id']}")] for x in channels]
            rows.append([("⬅️ Back", "main")])
            await self.edit(query, "❤️ **AUTO REACTIONS**\n\nChoose a channel:", kb(rows)); return
        if data == "settings":
            await self.edit(query, "⚙️ **BOT SETTINGS**\n\nYour configuration is stored per user and per channel. Use Bot Info and Help for setup guidance.", back()); return
        if data.startswith("channel:"):
            await self.show_channel(query, user_id, int(data.split(":", 1)[1])); return
        if data.startswith("join:"):
            chat_id = int(data.split(":", 1)[1]); item = await self.channel_for(user_id, chat_id)
            if item: await self.edit(query, "👥 **JOIN REQUEST MODE**\n\nChoose how new requests are handled.", join_menu(chat_id, item.get("mode", "auto")))
            return
        if data.startswith("joinmode:"):
            _, raw_id, mode = data.split(":", 2)
            if mode in JOIN_MODES:
                await self.repository.update_channel(user_id, int(raw_id), {"mode": mode})
                await self.show_channel(query, user_id, int(raw_id))
            return
        if data.startswith("timing:"):
            await self.edit(query, "⏱️ **APPROVAL TIMING**\n\nChoose a delay.", timing_menu(int(data.split(":", 1)[1]))); return
        if data.startswith("setdelay:"):
            _, raw_id, raw_value = data.split(":", 2); await self.repository.update_channel(user_id, int(raw_id), {"approval_delay": int(raw_value)}); await self.show_channel(query, user_id, int(raw_id)); return
        if data.startswith("setdelay_custom:"):
            self.states[user_id] = {"action": "approval_delay", "chat_id": int(data.split(":", 1)[1])}; await self.edit(query, "Send a delay in seconds: 0, 5, 10, 30, 60, or 300.", back()); return
        if data.startswith("reaction:"):
            await self.show_reaction(query, user_id, int(data.split(":", 1)[1])); return
        if data.startswith("radd:"):
            self.states[user_id] = {"action": "reaction_add", "chat_id": int(data.split(":", 1)[1])}; await self.edit(query, "Send an emoji and percentage, for example ❤️ 50. Total weights must sum to 100%.", back()); return
        if data.startswith("redit:"):
            self.states[user_id] = {"action": "reaction_edit", "chat_id": int(data.split(":", 1)[1])}; await self.edit(query, "Send the emoji and its new percentage, for example 🔥 30.", back()); return
        if data.startswith("rremove:"):
            self.states[user_id] = {"action": "reaction_remove", "chat_id": int(data.split(":", 1)[1])}; await self.edit(query, "Send the exact emoji to remove.", back()); return
        if data.startswith("rreset:"):
            chat_id = int(data.split(":", 1)[1]); await self.edit(query, "Reset all reaction weights?", kb([[('✅ Reset', f"rreset_confirm:{chat_id}")], [('⬅️ Cancel', f"reaction:{chat_id}")]])); return
        if data.startswith("rreset_confirm:"):
            chat_id = int(data.split(":", 1)[1]); await self.repository.update_channel(user_id, chat_id, {"reactions": [], "reaction_enabled": False}); await self.show_reaction(query, user_id, chat_id); return
        if data.startswith("rdelay:"):
            await self.edit(query, "⏱️ **REACTION DELAY**\n\nChoose a delay.", timing_menu(int(data.split(":", 1)[1]), True)); return
        if data.startswith("rsetdelay:"):
            _, raw_id, raw_value = data.split(":", 2); await self.repository.update_channel(user_id, int(raw_id), {"reaction_delay": int(raw_value)}); await self.show_reaction(query, user_id, int(raw_id)); return
        if data.startswith("rsetdelay_custom:"):
            self.states[user_id] = {"action": "reaction_delay", "chat_id": int(data.split(":", 1)[1])}; await self.edit(query, "Send a delay in seconds: 0, 2, 5, 10, or 30.", back()); return
        if data.startswith("rtoggle:"):
            chat_id = int(data.split(":", 1)[1]); item = await self.channel_for(user_id, chat_id)
            if item and not item.get("reaction_enabled"):
                valid, status = validate_reaction_set(item.get("reactions", []))
                if not valid: await self.edit(query, f"❌ {status}", reaction_menu(chat_id, False)); return
            await self.repository.update_channel(user_id, chat_id, {"reaction_enabled": not item.get("reaction_enabled", False)}); await self.show_reaction(query, user_id, chat_id); return
        if data.startswith("preview:"):
            item = await self.channel_for(user_id, int(data.split(":", 1)[1])); reactions = (item or {}).get("reactions", []); await query.answer(choose_weighted_reaction(reactions) or "Configure reactions first."); return
        if data.startswith("welcome:"):
            await self.show_welcome(query, user_id, int(data.split(":", 1)[1])); return
        if data.startswith("welcomestate:"):
            _, raw_id, raw_state = data.split(":", 2); await self.repository.update_channel(user_id, int(raw_id), {"welcome_enabled": raw_state == "1"}); await self.show_welcome(query, user_id, int(raw_id)); return
        if data.startswith("welcomeedit:"):
            self.states[user_id] = {"action": "welcome_edit", "chat_id": int(data.split(":", 1)[1])}; await self.edit(query, "Send the new welcome message. You can use {first_name}, {last_name}, {username}, {user_id}, {chat_title}.", back()); return
        if data.startswith("welcomepreview:"):
            item = await self.channel_for(user_id, int(data.split(":", 1)[1])); template = (item or {}).get("welcome_message", "Welcome {first_name}!"); await query.answer(render_welcome(template, query.from_user, "chat")); return
        if data.startswith("targets:"):
            await self.show_targets(query, user_id, int(data.split(":", 1)[1])); return
        if data.startswith("target:"):
            _, raw_id, target = data.split(":", 2); chat_id = int(raw_id); item = await self.channel_for(user_id, chat_id); current = set((item or {}).get("targets", ["all"]))
            if target == "all": current = {"all"}
            else:
                current.discard("all"); current.symmetric_difference_update({target}); current = current or {"all"}
            await self.repository.update_channel(user_id, chat_id, {"targets": sorted(current)}); await self.show_targets(query, user_id, chat_id); return
        if data.startswith("toggle:"):
            chat_id = int(data.split(":", 1)[1]); item = await self.channel_for(user_id, chat_id); await self.repository.update_channel(user_id, chat_id, {"enabled": not item.get("enabled", True)}); await self.show_channel(query, user_id, chat_id); return
        if data.startswith("remove:"):
            chat_id = int(data.split(":", 1)[1]); await self.edit(query, "Remove this channel configuration? Telegram permissions will not be changed.", kb([[('✅ Remove', f"remove_confirm:{chat_id}")], [('⬅️ Cancel', f"channel:{chat_id}")]])); return
        if data.startswith("remove_confirm:"):
            chat_id = int(data.split(":", 1)[1]); await self.repository.remove_channel(user_id, chat_id); await self.show_channels(query, user_id); return
        if data == "owner":
            if self.is_owner(user_id): await self.edit(query, "👑 **BOT CREATOR SETTINGS**\n\nManage force subscription, admins, and diagnostics.", owner_menu())
            return
        if data == "botstatus":
            await self.owner_page(query, "BOT STATUS", f"Uptime: {int(time.monotonic() - STARTED_AT)} seconds\nJoin queue: {self.processor.join_queue.qsize()}\nReaction queue: {self.processor.reaction_queue.qsize()}"); return
        if data == "dbstatus":
            await self.owner_page(query, "DATABASE STATUS", f"MongoDB: {'connected' if self.repository.connected else 'temporary in-memory fallback'}\nConfigured channels: {await self.repository.count_channels()}"); return
        if data == "secrets":
            flags = self.settings.secret_status; body = "\n".join(f"{key}: {'configured' if value else 'missing'}" for key, value in flags.items()); await self.owner_page(query, "SECRETS STATUS", body); return
        if data in {"config", "maintenance", "security"}:
            await self.owner_page(query, data.upper(), "No additional settings are required here. Runtime configuration comes from Render Secrets and Telegram permissions."); return
        if data == "admins":
            admins = await self.repository.list_admins(); body = "\n".join(str(x.get("user_id")) for x in admins) or "No delegated admins."; await self.owner_page(query, "BOT ADMINS", body, [[("➕ Add Admin", "adminadd"), ("➖ Remove Admin", "adminremove")]]); return
        if data in {"adminadd", "adminremove"}:
            self.states[user_id] = {"action": "admin_add" if data == "adminadd" else "admin_remove"}; await self.edit(query, "Send the numeric Telegram user ID, or /cancel.", back("admins")); return
        if data == "broadcast":
            await self.owner_page(query, "BROADCAST", "Broadcast is disabled by default. This prevents accidental messaging of users without an explicit recipient policy."); return
        if data == "force":
            items = await self.repository.list_force_subscribe(); lines = "\n".join(f"Slot {x.get('slot')}: {x.get('title', 'configured')} ({'on' if x.get('enabled', True) else 'off'})" for x in items) or "No force-subscribe channels configured."; rows = [[("🔗 Add Slot 1", "forceadd:1"), ("🔗 Add Slot 2", "forceadd:2")], [("🔗 Add Slot 3", "forceadd:3"), ("🔗 Add Slot 4", "forceadd:4")]]; await self.owner_page(query, "FORCE SUBSCRIBE", lines, rows); return
        if data.startswith("forceadd:"):
            slot = int(data.split(":", 1)[1]); self.states[user_id] = {"action": "force_add", "slot": slot}; await self.edit(query, "Send @username or a -100… chat ID for this force-subscribe slot, or /cancel.", back("force")); return
        if data == "forcecheck":
            if await self.allowed(user_id): await self.edit(query, "✅ Membership verified.", main_menu(self.is_owner(user_id)))
            else: await self.edit(query, "❌ You still need to join the required channels.", await self.force_markup())
            return
        if data.startswith("noop:"):
            return

    async def health(self, _):
        return web.Response(text="OK", content_type="text/plain")

    async def ping(self, _):
        return web.Response(text="pong", content_type="text/plain")

    async def status(self, _):
        return web.json_response({
            "status": "ok" if self.bot_status == "running" else "starting",
            "uptime_seconds": int(time.monotonic() - STARTED_AT),
            "bot_status": self.bot_status,
            "database_status": "connected" if self.repository.connected else "fallback",
        })

    async def root(self, _):
        return web.Response(text="Replit Telegram bot is running", content_type="text/plain")

    async def start_http(self):
        log.info("Web server starting...")
        app = web.Application()
        app.add_routes([
            web.get("/", self.root),
            web.get("/health", self.health),
            web.get("/ping", self.ping),
            web.get("/status", self.status),
        ])
        self.http_runner = web.AppRunner(app)
        await self.http_runner.setup()
        self.http_site = web.TCPSite(self.http_runner, "0.0.0.0", self.settings.port)
        await self.http_site.start()
        log.info("Web server listening on 0.0.0.0:%s", self.settings.port)

    async def reconnect_database(self):
        while not self.stopping:
            if not self.repository.connected:
                await self.repository.reconnect()
            await asyncio.sleep(30)

    async def run(self):
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)

        # Bind the liveness server first. It must remain available while the
        # database and Telegram client connect or recover.
        await self.start_http()
        log.info("Telegram bot starting...")
        database_connected = await self.repository.connect()
        log.info("Database connection status: %s", "connected" if database_connected else "temporary in-memory fallback")
        self.db_task = asyncio.create_task(self.reconnect_database(), name="database-reconnect")

        try:
            await self.client.start()
            self.me = await self.client.get_me()
            await self.register_handlers()
            await self.processor.start()
            self.bot_status = "running"
            log.info("Telegram bot started as @%s", self.me.username or self.me.id)
            await stop_event.wait()
        except Exception:
            self.bot_status = "error"
            log.exception("Telegram bot startup/runtime failed")
            await stop_event.wait()
        finally:
            self.stopping = True
            if self.db_task:
                self.db_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.db_task
            await self.processor.stop()
            if self.http_runner:
                await self.http_runner.cleanup()
            if getattr(self.client, "is_connected", False):
                await self.client.stop()
            await self.repository.close()


def main():
    settings = load_settings()
    asyncio.run(BotApplication(settings).run())


if __name__ == "__main__":
    main()
