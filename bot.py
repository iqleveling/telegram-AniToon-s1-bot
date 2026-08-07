import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("AniToonBot")


# ============================================================
# RENDER ENVIRONMENT
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

PORT = int(os.environ.get("PORT", "10000"))

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ============================================================
# TELEGRAM CLIENT
# ============================================================

bot = Client(
    "anitoon_rename_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


# ============================================================
# USER FILES
# ============================================================

user_files = {}


# ============================================================
# RENDER HEALTH
# ============================================================

async def health(request):

    return web.json_response(
        {
            "status": "online",
            "bot": "AniToon Rename Bot",
        }
    )


async def start_web_server():

    application = web.Application()

    application.router.add_get("/", health)
    application.router.add_get("/health", health)

    runner = web.AppRunner(application)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    log.info("WEB SERVER: http://0.0.0.0:%s", PORT)


# ============================================================
# DEBUG — LOG EVERY MESSAGE
# ============================================================

@bot.on_message(filters.all, group=999)
async def debug_all_messages(client, message):

    try:

        user = message.from_user

        username = (
            f"@{user.username}"
            if user and user.username
            else "no_username"
        )

        text = message.text or message.caption or "[media]"

        log.info(
            "📩 UPDATE RECEIVED | user=%s | id=%s | text=%s",
            username,
            user.id if user else "unknown",
            text[:200],
        )

    except Exception:

        log.exception(
            "Error while logging incoming update"
        )


# ============================================================
# START
# ============================================================

@bot.on_message(
    filters.private & filters.command("start"),
    group=0,
)
async def start_handler(client, message):

    log.info(
        "START COMMAND from user %s",
        message.from_user.id,
    )

    await message.reply_text(
        "👋 **Welcome to AniToon's Rename Bot!**\n\n"
        "📁 Send me a file.\n"
        "✏️ Then send the new filename.\n\n"
        "⚡ I will rename it and send it back.\n\n"
        "📚 /help\n"
        "❌ /cancel"
    )


# ============================================================
# HELP
# ============================================================

@bot.on_message(
    filters.private & filters.command("help"),
    group=0,
)
async def help_handler(client, message):

    log.info(
        "HELP COMMAND from user %s",
        message.from_user.id,
    )

    await message.reply_text(
        "📚 **AniToon Rename Bot**\n\n"
        "/start - Start the bot\n"
        "/help - Help\n"
        "/cancel - Cancel\n\n"
        "📁 Send a file → send the new filename."
    )


# ============================================================
# CANCEL
# ============================================================

@bot.on_message(
    filters.private & filters.command("cancel"),
    group=0,
)
async def cancel_handler(client, message):

    user_id = message.from_user.id

    data = user_files.pop(user_id, None)

    if data:

        path = data["path"]

        if path.exists():
            path.unlink()

        await message.reply_text(
            "❌ Current file cancelled."
        )

    else:

        await message.reply_text(
            "ℹ️ No active file."
        )


# ============================================================
# RECEIVE FILE
# ============================================================

@bot.on_message(
    filters.private
    & (
        filters.document
        | filters.video
        | filters.audio
    ),
    group=0,
)
async def file_handler(client, message):

    user_id = message.from_user.id

    log.info(
        "📁 FILE RECEIVED from user %s",
        user_id,
    )

    # Remove previous file

    old = user_files.pop(user_id, None)

    if old:

        old_path = old["path"]

        if old_path.exists():
            old_path.unlink()

    # Determine filename

    if message.document:

        filename = (
            message.document.file_name
            or f"file_{message.id}"
        )

        size = message.document.file_size or 0

    elif message.video:

        filename = (
            message.video.file_name
            or f"video_{message.id}.mp4"
        )

        size = message.video.file_size or 0

    else:

        filename = (
            message.audio.file_name
            or f"audio_{message.id}.mp3"
        )

        size = message.audio.file_size or 0

    path = DOWNLOAD_DIR / (
        f"{user_id}_{message.id}_{filename}"
    )

    user_files[user_id] = {
        "message": message,
        "path": path,
        "filename": filename,
    }

    await message.reply_text(
        "📁 **File received!**\n\n"
        f"📄 `{filename}`\n"
        f"📦 `{size / 1024 / 1024:.2f} MB`\n\n"
        "✏️ Send the new filename."
    )


# ============================================================
# RECEIVE NEW NAME
# ============================================================

@bot.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        ["start", "help", "cancel"]
    ),
    group=0,
)
async def rename_handler(client, message):

    user_id = message.from_user.id

    data = user_files.get(user_id)

    if not data:

        await message.reply_text(
            "📁 Send a file first."
        )

        return

    new_name = os.path.basename(
        message.text.strip()
    )

    if not new_name:

        await message.reply_text(
            "❌ Invalid filename."
        )

        return

    original_name = data["filename"]

    extension = Path(original_name).suffix

    if (
        not Path(new_name).suffix
        and extension
    ):

        new_name += extension

    old_path = data["path"]

    new_path = DOWNLOAD_DIR / (
        f"{user_id}_{new_name}"
    )

    status = await message.reply_text(
        "⬇️ **Downloading...**"
    )

    try:

        log.info(
            "Downloading file for user %s",
            user_id,
        )

        await client.download_media(
            data["message"],
            file_name=str(old_path),
        )

        await status.edit_text(
            "⚡ **Renaming...**"
        )

        old_path.rename(new_path)

        await status.edit_text(
            "⬆️ **Uploading...**"
        )

        await client.send_document(
            chat_id=message.chat.id,
            document=str(new_path),
            caption=(
                "✅ **Renamed successfully!**\n\n"
                f"📄 `{new_name}`"
            ),
        )

        if new_path.exists():
            new_path.unlink()

        user_files.pop(user_id, None)

        await status.delete()

    except Exception as error:

        log.exception(
            "Rename error for user %s",
            user_id,
        )

        for path in (old_path, new_path):

            try:

                if path.exists():
                    path.unlink()

            except Exception:
                pass

        user_files.pop(user_id, None)

        await status.edit_text(
            f"❌ **Rename failed**\n\n`{error}`"
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    log.info("======================================")
    log.info("🚀 STARTING ANITOON RENAME BOT")
    log.info("======================================")

    await start_web_server()

    log.info("Connecting to Telegram...")

    await bot.start()

    me = await bot.get_me()

    log.info(
        "CONNECTED: @%s | ID=%s",
        me.username,
        me.id,
    )

    log.info("✅ BOT IS RUNNING")
    log.info("⚡ Waiting for Telegram updates...")

    await asyncio.Event().wait()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except Exception:

        log.exception(
            "💥 FATAL BOT ERROR"
        )

        raise
