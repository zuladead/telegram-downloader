import asyncio
import logging
import math
import os
import platform
import shutil
import traceback

from telegram import Message, Update
from telegram.ext import ContextTypes, filters
from telegram.helpers import escape_markdown

from ..middlewares.auth import auth_required
from ..middlewares.handlers import (
    callback_query_handler,
    command_handler,
    message_handler,
)
from ..models import DownloadFile, downloading_files
from ..utils import check_file_exists, env, get_file
from ..utils.file_naming import build_download_name

logger = logging.getLogger(__name__)

BOT_TOKEN = env.BOT_TOKEN
BOT_API_DIR = env.BOT_API_DIR
DOWNLOAD_TO_DIR = env.DOWNLOAD_TO_DIR

TOKEN_SUB_DIR = BOT_TOKEN.replace(":", "") if os.name == "nt" else BOT_TOKEN


def get_message_file(message: Message):
    """Return a supported Telegram file and a usable original file name."""
    telegram_file = message.document or message.video or message.audio
    if telegram_file is None:
        raise ValueError("Message does not contain a supported file")

    file_name = telegram_file.file_name
    if not file_name:
        media_type = "video" if message.video else "audio"
        extension = ".mp4" if message.video else ".mp3"
        file_name = f"{media_type}_{telegram_file.file_unique_id}{extension}"

    return telegram_file, file_name


def resolve_file_path(file_path: str) -> str:
    """Resolve relative Bot API paths while preserving local-mode absolute paths."""
    if os.path.isabs(file_path):
        return file_path
    return os.path.join(BOT_API_DIR, TOKEN_SUB_DIR, file_path.lstrip("/\\\\"))


@command_handler("status")
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send downloading files status to the user."""
    if not downloading_files:
        await update.message.reply_text("No files are being downloaded at the moment.")
        return

    status_message = "*Downloading files status:*\nPage 1\n"

    for i, file in enumerate(downloading_files.values(), start=1):
        display_name = escape_markdown(file.file_name, version=2, entity_type="code")
        file_status = (
            f"> 📄 *File name:*   `{display_name}`\n"
            f"> 💾 *File size:*   `{file.file_size_mb}`\n"
            f"> ⏰ *Start time:*   `{file.start_datetime}`\n"
            f"> ⏱ *Duration:*   `{file.current_download_duration}`\n"
            f"> 🔻 *Retries:*   `{file.download_retries}`\n"
            f"> 🔄 *Status:*   `{file.status}`\n\n"
        )
        status_message += file_status

        if i % 2 == 0 or i == len(downloading_files):
            if i > 2:
                status_message = f"Page {math.ceil(i / 2)}\n" + status_message

            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=status_message,
                parse_mode="MarkdownV2",
            )
            status_message = ""
            await asyncio.sleep(0.3)


SUPPORTED_FILES = filters.Document.ALL | filters.VIDEO | filters.AUDIO


@message_handler(SUPPORTED_FILES & filters.ChatType.PRIVATE)
@auth_required
async def download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Automatically download a supported file sent in an authorized private chat."""
    logger.info("Download command received")
    await start_download(update.message, context)


@callback_query_handler()
@auth_required
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep handling confirmation buttons from messages created before auto-download."""
    logger.info("Button command received")
    query = update.callback_query

    await query.answer()
    message = update.effective_message.reply_to_message
    await update.effective_message.edit_reply_markup(reply_markup=None)

    if query.data == "yes":
        await start_download(message, context)
    else:
        logger.info("Download cancelled")
        await message.reply_text("Download cancelled.")


async def start_download(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the existing Yes branch for a document, video, or audio message."""
    telegram_file, file_name = get_message_file(message)
    file_name = build_download_name(message, telegram_file, file_name)
    display_name = escape_markdown(file_name, version=2, entity_type="code")
    file_id = telegram_file.file_id
    file_size = telegram_file.file_size or 0

    try:
        check_file_exists(file_id, file_name)
    except Exception as e:
        logger.error(f"Error checking file exists: {e}")
        await message.reply_text(
            f"⛔ File already exists\\!\nError:```\n{e}```",
            parse_mode="MarkdownV2",
        )
        return

    logger.info("Downloading file...")
    download_file = DownloadFile(file_id, file_name, file_size)
    downloading_files[file_id] = download_file
    await message.reply_text("⬇️ Downloading file...")

    try:
        new_file = await get_file(context.bot, download_file)
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        traceback.print_exc()
        downloading_files.pop(file_id)
        await message.reply_text(
            (
                f"⛔ Error downloading file\n"
                f"> 📄 *File name:*   `{display_name}`\n"
                f"> 💾 *File size:*   `{download_file.file_size_mb}`\n"
                f"```\n{e}```"
            ),
            parse_mode="MarkdownV2",
        )
        return
    else:
        download_file.download_complete()

    file_path = new_file.file_path
    current_file_path = resolve_file_path(file_path)
    move_to_path = os.path.join(DOWNLOAD_TO_DIR, file_name)
    display_path = escape_markdown(move_to_path, version=2, entity_type="code")

    try:
        os.makedirs(DOWNLOAD_TO_DIR, exist_ok=True)
        os.rename(current_file_path, move_to_path)
    except Exception as rename_error:
        logger.error(f"Error RENAMING file: {rename_error}")
        try:
            await asyncio.to_thread(shutil.move, current_file_path, move_to_path)
        except Exception as move_error:
            logger.error(f"Error MOVING file: {move_error}")
            downloading_files.pop(file_id)
            await message.reply_text(
                (
                    f"⛔ Error moving file\n"
                    f"> 📂 *File path:*   `{file_path}`\n"
                    f"> 📂 *Move to path:*   `{display_path}`\n"
                    f"Rename error:\n```\n{rename_error}```\n"
                    f"Move error:\n```\n{move_error}```"
                ),
                parse_mode="MarkdownV2",
            )
            return

    download_file.move_complete()
    downloading_files.pop(file_id)

    if platform.system() == "Linux":
        os.chmod(move_to_path, 0o664)

    response_message = (
        f"✅ File downloaded successfully\\.\n\n"
        f"> 📄 *File name:*   `{display_name}`\n"
        f"> 📂 *File path:*   `{file_path}`\n"
        f"> 💾 *File size:*   `{download_file.file_size_mb}`\n"
        f"> 🔻 *Retries:*   `{download_file.download_retries}`\n"
        f"> ⏱ *Download Duration:*   `{download_file.download_duration}`\n"
        f"> ⏱ *Moving Duration:*   `{download_file.move_duration}`\n"
        f"> ⏱ *Total Duration:*   `{download_file.total_duration}`"
    )
    await message.reply_text(response_message, parse_mode="MarkdownV2")
