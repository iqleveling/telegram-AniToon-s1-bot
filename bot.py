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

log = logging.getLogger("AniToonRenameBot")


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

PORT = int(os.environ.get("PORT", "10000"))


# ============================================================
# FILE STORAGE
# ============================================================

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

user_files = {}


# ============================================================
# TELEGRAM BOT
# ============================================================

bot = Client(
    "anitoon_rename_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


# ============================================================
# HEALTH SERVER
# ============================================================

async def health(request):
    return web.json_response(
        {
            "status": "ok",
            "bot": "AniToon Rename Bot",
        }
    )


async def start_web_server():

    server = web.Application()

    server.router.add_get("/", health)
    server.router.add_get("/health", health)

    runner = web.AppRunner(server)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    log.info("WEB SERVER: running on port %s", PORT)


# ============================================================
# /START
# ============================================================

@bot.on_message(
    filters.private & filters.command("start")
)
async def start_handler(client, message):

    await message.reply_text(
        "👋 **Welcome to AniToon's Rename Bot!**\n\n"
        "📁 Send me a file and I will rename it.\n\n"
        "⚡ **How it works:**\n"
        "1️⃣ Send your file\n"
        "2️⃣ Send the new filename\n"
        "3️⃣ Bot renames it\n"
        "4️⃣ Bot sends it back\n\n"
        "📚 /help\n"
        "❌ /cancel"
    )


# ============================================================
# /HELP
# ============================================================

@bot.on_message(
    filters.private & filters.command("help")
)
async def help_handler(client, message):

    await message.reply_text(
        "📚 **AniToon Rename Bot**\n\n"
        "📁 Send a file\n"
        "✏️ Send the new filename\n"
        "⚡ I'll rename it and send it back.\n\n"
        "**Commands**\n"
        "/start - Start bot\n"
        "/help - Help\n"
        "/cancel - Cancel current file"
    )


# ============================================================
# /CANCEL
# ============================================================

@bot.on_message(
    filters.private & filters.command("cancel")
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
            "ℹ️ There is no active file."
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
    )
)
async def file_handler(client, message):

    user_id = message.from_user.id

    # Remove previous file
    old = user_files.pop(user_id, None)

    if old:

        old_path = old["path"]

        if old_path.exists():
            old_path.unlink()

    # Get original filename
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

    temp_path = DOWNLOAD_DIR / (
        f"{user_id}_{message.id}_{filename}"
    )

    user_files[user_id] = {
        "message": message,
        "path": temp_path,
        "filename": filename,
    }

    size_mb = size / (1024 * 1024)

    await message.reply_text(
        "📁 **File received!**\n\n"
        f"📄 `{filename}`\n"
        f"📦 `{size_mb:.2f} MB`\n\n"
        "✏️ Now send the **new filename**.\n\n"
        "Example:\n"
        "`My Movie 2026`"
    )


# ============================================================
# RECEIVE NEW NAME
# ============================================================

@bot.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        ["start", "help", "cancel"]
    )
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

    if not Path(new_name).suffix and extension:

        new_name += extension

    old_path = data["path"]

    new_path = DOWNLOAD_DIR / (
        f"{user_id}_{new_name}"
    )

    status = await message.reply_text(
        "⬇️ **Downloading...**"
    )

    try:

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
            message.chat.id,
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
            "Rename failed for user %s",
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
            "❌ **Rename failed.**\n\n"
            f"`{error}`"
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    log.info("======================================")
    log.info("🚀 Starting AniToon Rename Bot")
    log.info("======================================")

    await start_web_server()

    log.info("Connecting to Telegram...")

    await bot.start()

    me = await bot.get_me()

    log.info(
        "TELEGRAM: connected as @%s",
        me.username,
    )

    log.info("✅ BOT IS RUNNING")
    log.info("⚡ Ready to rename files")

    await asyncio.Event().wait()


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except Exception:

        log.exception(
            "FATAL ERROR: bot stopped"
        )

        raise
