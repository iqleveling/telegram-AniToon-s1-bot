import asyncio
import os
from pathlib import Path

from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message


# ============================================================
# CONFIG — values come from Render Environment Variables
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

PORT = int(os.environ.get("PORT", "10000"))

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ============================================================
# TELEGRAM CLIENT
# ============================================================

app = Client(
    "telegram_anitoon_s1_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


# ============================================================
# TEMPORARY USER FILE STORAGE
# ============================================================

user_files = {}


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

async def health(request):
    return web.Response(
        text="Telegram AniToon Rename Bot is running!"
    )


async def start_web_server():
    web_app = web.Application()

    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)

    runner = web.AppRunner(web_app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    print(f"🌐 Web server running on port {PORT}")


# ============================================================
# START COMMAND
# ============================================================

@app.on_message(filters.private & filters.command("start"))
async def start_command(client: Client, message: Message):

    await message.reply_text(
        "👋 **Welcome to AniToon's Rename Bot!**\n\n"
        "📁 Send me a file.\n"
        "✏️ Then send the new filename.\n\n"
        "⚡ I will rename it and send it back.\n\n"
        "Use /help for commands."
    )


# ============================================================
# HELP COMMAND
# ============================================================

@app.on_message(filters.private & filters.command("help"))
async def help_command(client: Client, message: Message):

    await message.reply_text(
        "📚 **Commands**\n\n"
        "/start - Start the bot\n"
        "/help - Show help\n"
        "/cancel - Cancel current file\n\n"
        "📁 Send a file → send the new filename."
    )


# ============================================================
# CANCEL COMMAND
# ============================================================

@app.on_message(filters.private & filters.command("cancel"))
async def cancel_command(client: Client, message: Message):

    user_id = message.from_user.id

    data = user_files.pop(user_id, None)

    if not data:
        await message.reply_text(
            "ℹ️ You don't have an active file."
        )
        return

    file_path = data["path"]

    if file_path.exists():
        file_path.unlink()

    await message.reply_text(
        "❌ Current file cancelled."
    )


# ============================================================
# RECEIVE FILE
# ============================================================

@app.on_message(
    filters.private
    & (
        filters.document
        | filters.video
        | filters.audio
    )
)
async def receive_file(client: Client, message: Message):

    user_id = message.from_user.id

    # Remove previous pending file
    old_data = user_files.pop(user_id, None)

    if old_data:

        old_path = old_data["path"]

        if old_path.exists():
            old_path.unlink()

    # Determine filename
    if message.document:

        original_name = (
            message.document.file_name
            or f"file_{message.id}"
        )

        file_size = message.document.file_size or 0

    elif message.video:

        original_name = (
            message.video.file_name
            or f"video_{message.id}.mp4"
        )

        file_size = message.video.file_size or 0

    else:

        original_name = (
            message.audio.file_name
            or f"audio_{message.id}.mp3"
        )

        file_size = message.audio.file_size or 0

    # Temporary local filename
    temp_path = DOWNLOAD_DIR / (
        f"{user_id}_{message.id}_{original_name}"
    )

    user_files[user_id] = {
        "message": message,
        "path": temp_path,
        "original_name": original_name,
        "size": file_size,
    }

    size_mb = file_size / (1024 * 1024)

    await message.reply_text(
        f"📁 **File received!**\n\n"
        f"**Name:** `{original_name}`\n"
        f"**Size:** `{size_mb:.2f} MB`\n\n"
        "✏️ **Now send the new filename.**\n\n"
        "Example:\n"
        "`My Movie 2026`"
    )


# ============================================================
# RECEIVE NEW FILENAME
# ============================================================

@app.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        ["start", "help", "cancel"]
    )
)
async def rename_file(client: Client, message: Message):

    user_id = message.from_user.id

    data = user_files.get(user_id)

    if not data:

        await message.reply_text(
            "📁 Please send a file first."
        )

        return

    original_path = data["path"]
    original_name = data["original_name"]

    new_name = message.text.strip()

    # Prevent paths such as ../../something
    new_name = os.path.basename(new_name)

    if not new_name:

        await message.reply_text(
            "❌ Invalid filename."
        )

        return

    # Keep original extension if user didn't enter one
    original_extension = Path(original_name).suffix

    if not Path(new_name).suffix and original_extension:

        new_name += original_extension

    new_path = DOWNLOAD_DIR / (
        f"{user_id}_{new_name}"
    )

    status = await message.reply_text(
        "⬇️ **Downloading file...**"
    )

    try:

        # ====================================================
        # DOWNLOAD
        # ====================================================

        await client.download_media(
            data["message"],
            file_name=str(original_path),
        )

        # ====================================================
        # RENAME
        # ====================================================

        await status.edit_text(
            "⚡ **Renaming file...**"
        )

        original_path.rename(new_path)

        # ====================================================
        # UPLOAD
        # ====================================================

        await status.edit_text(
            "⬆️ **Uploading renamed file...**"
        )

        await client.send_document(
            chat_id=message.chat.id,
            document=str(new_path),
            caption=(
                f"✅ **Renamed successfully!**\n\n"
                f"📁 `{new_name}`"
            ),
        )

        # ====================================================
        # CLEANUP
        # ====================================================

        if new_path.exists():
            new_path.unlink()

        user_files.pop(user_id, None)

        await status.delete()

    except Exception as error:

        # Cleanup temporary files
        for path in [original_path, new_path]:

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

    print("🚀 Starting AniToon Rename Bot...")

    await start_web_server()

    print("🤖 Connecting to Telegram...")

    await app.start()

    print("✅ Telegram bot is running!")
    print("⚡ Ready to rename files.")

    # Keep process alive
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
