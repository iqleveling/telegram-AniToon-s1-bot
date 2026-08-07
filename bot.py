import asyncio
import os
import time
from pathlib import Path

from aiohttp import web
from pyrogram import Client, filters
from pyrogram.types import Message

# ============================================================
# CONFIG
# ============================================================

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ============================================================
# BOT
# ============================================================

app = Client(
    "TelegramRenameBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=32,
)

# Stores the latest file sent by each user
user_files = {}

# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

async def health(request):
    return web.Response(text="Telegram Rename Bot is running!")


async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health)
    server.router.add_get("/health", health)

    runner = web.AppRunner(server)
    await runner.setup()

    port = int(os.environ.get("PORT", "10000"))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

# ============================================================
# PROGRESS
# ============================================================

async def progress(current, total, message: Message, action: str, start_time):
    now = time.monotonic()

    # Avoid editing Telegram messages too frequently
    if now - progress.last_update < 2:
        return

    progress.last_update = now

    percentage = current * 100 / total

    elapsed = now - start_time

    if elapsed > 0:
        speed = current / elapsed
        remaining = total - current

        if speed > 0:
            eta = int(remaining / speed)
        else:
            eta = 0
    else:
        eta = 0

    text = (
        f"⚡ **{action}**\n\n"
        f"**Progress:** `{percentage:.1f}%`\n"
        f"**Size:** `{current / 1024 / 1024:.1f} MB / "
        f"{total / 1024 / 1024:.1f} MB`\n"
        f"**ETA:** `{eta}s`"
    )

    try:
        await message.edit_text(text)
    except Exception:
        pass


progress.last_update = 0

# ============================================================
# START
# ============================================================

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):

    await message.reply_text(
        "👋 **Welcome to Fast Rename Bot!**\n\n"
        "📤 Send me any file.\n"
        "✏️ Then send the new filename.\n\n"
        "⚡ I will rename it and send it back."
    )

# ============================================================
# HELP
# ============================================================

@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):

    await message.reply_text(
        "📚 **Commands**\n\n"
        "`/start` - Start the bot\n"
        "`/help` - Show help\n"
        "`/cancel` - Cancel current file\n\n"
        "📁 Send a file → enter the new filename."
    )

# ============================================================
# CANCEL
# ============================================================

@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):

    user_id = message.from_user.id

    if user_id in user_files:
        data = user_files.pop(user_id)

        file_path = data.get("path")

        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

        await message.reply_text("❌ Current operation cancelled.")
    else:
        await message.reply_text("ℹ️ You don't have an active file.")

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

    # Remove previous file for this user
    old = user_files.pop(user_id, None)

    if old:
        old_path = old.get("path")

        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

    status = await message.reply_text(
        "📥 **File received!**\n\n"
        "Preparing download..."
    )

    try:
        # Get original filename
        if message.document:
            original_name = message.document.file_name or "file"
            file_size = message.document.file_size

        elif message.video:
            original_name = (
                message.video.file_name
                or f"video_{message.id}.mp4"
            )
            file_size = message.video.file_size

        elif message.audio:
            original_name = (
                message.audio.file_name
                or f"audio_{message.id}.mp3"
            )
            file_size = message.audio.file_size

        else:
            await status.edit_text("❌ Unsupported file.")
            return

        # Safe temporary filename
        temp_name = f"{user_id}_{message.id}_{original_name}"
        file_path = DOWNLOAD_DIR / temp_name

        # Save information
        user_files[user_id] = {
            "message": message,
            "path": str(file_path),
            "original_name": original_name,
            "size": file_size,
        }

        await status.edit_text(
            f"📁 **File:** `{original_name}`\n"
            f"📦 **Size:** `{file_size / 1024 / 1024:.2f} MB`\n\n"
            "✏️ **Now send me the new filename.**\n\n"
            "Example:\n"
            "`My Movie 2026`"
        )

    except Exception as e:
        await status.edit_text(
            f"❌ Error:\n`{e}`"
        )

# ============================================================
# RECEIVE NEW FILENAME
# ============================================================

@app.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "help",
            "cancel",
        ]
    )
)
async def rename_file(client: Client, message: Message):

    user_id = message.from_user.id

    if user_id not in user_files:
        await message.reply_text(
            "📤 Please send a file first."
        )
        return

    data = user_files[user_id]

    original_path = Path(data["path"])
    original_name = data["original_name"]

    new_name = message.text.strip()

    # Remove accidental path characters
    new_name = os.path.basename(new_name)

    if not new_name:
        await message.reply_text(
            "❌ Please enter a valid filename."
        )
        return

    # Keep original extension if user didn't provide one
    original_extension = Path(original_name).suffix

    if not Path(new_name).suffix and original_extension:
        new_name += original_extension

    # Final path
    new_path = original_path.with_name(
        f"{user_id}_{int(time.time())}_{new_name}"
    )

    status = await message.reply_text(
        "⬇️ **Downloading file...**"
    )

    try:

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        progress.last_update = 0
        download_start = time.monotonic()

        await client.download_media(
            data["message"],
            file_name=str(original_path),
            progress=progress,
            progress_args=(
                status,
                "Downloading",
                download_start,
            ),
        )

        # ----------------------------------------------------
        # RENAME
        # ----------------------------------------------------

        await status.edit_text(
            "⚡ **Renaming...**"
        )

        # This operation itself is extremely fast
        os.rename(
            original_path,
            new_path,
        )

        # ----------------------------------------------------
        # UPLOAD
        # ----------------------------------------------------

        await status.edit_text(
            "⬆️ **Uploading renamed file...**"
        )

        progress.last_update = 0
        upload_start = time.monotonic()

        await client.send_document(
            chat_id=message.chat.id,
            document=str(new_path),
            caption=(
                f"📁 `{new_name}`\n\n"
                "⚡ Renamed successfully!"
            ),
            progress=progress,
            progress_args=(
                status,
                "Uploading",
                upload_start,
            ),
        )

        # ----------------------------------------------------
        # CLEANUP
        # ----------------------------------------------------

        if os.path.exists(new_path):
            os.remove(new_path)

        user_files.pop(user_id, None)

        await status.delete()

    except Exception as e:

        # Cleanup
        for path in (original_path, new_path):

            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        user_files.pop(user_id, None)

        await status.edit_text(
            f"❌ **Rename failed**\n\n"
            f"`{e}`"
        )

# ============================================================
# MAIN
# ============================================================

async def main():

    if not API_ID:
        raise ValueError("API_ID is missing.")

    if not API_HASH:
        raise ValueError("API_HASH is missing.")

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing.")

    await start_web_server()

    print("🌐 Render web server started.")
    print("🤖 Starting Telegram bot...")

    await app.start()

    print("✅ Telegram Rename Bot is running!")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
