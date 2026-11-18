import asyncio
import logging
import os

from telegram import Bot, File
from telegram.error import NetworkError

from src.utils.env import env

from ..models import DownloadFile, downloading_files
from ..safe_logging import safe_error

logger = logging.getLogger(__name__)

# Retry constants
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 5

# Environment variables
DOWNLOAD_TO_DIR = env.DOWNLOAD_TO_DIR


async def get_file(bot: Bot, file: DownloadFile) -> File:
    """
    Download a file from Telegram with retry logic.
    Args:
        bot (Bot): The bot instance used to download the file.
        file (DownloadFile): The download file object (containing file_id).
    Returns:
        File: The downloaded file object.
    Raises:
        Exception: If the maximum number of retries is reached/non network error occurs
        or file already exists.
    """
    for i in range(MAX_RETRIES):
        # Log attempt
        task_id = getattr(file, 'task_id', 'legacy')
        logger.info('task=%s state=requesting attempt=%s/%s', task_id, i + 1, MAX_RETRIES)
        file.download_retries = i

        # Check if file exists in directory already
        check_file_exists(file.file_id, file.file_name, check_downloading_files=False)

        try:
            new_file = await bot.get_file(file.file_id, read_timeout=1800)
            logger.info('task=%s state=downloaded attempt=%s', task_id, i + 1)
            break
        except NetworkError as e:
            logger.warning('task=%s state=retry_wait attempt=%s error=%s',
                           task_id, i + 1, safe_error(e))
            if i + 1 < MAX_RETRIES:
                await asyncio.sleep(min(60, INITIAL_RETRY_DELAY * 2 ** i))

    else:
        raise Exception("Max retries reached")

    return new_file


def check_file_exists(
    file_id: str, file_name: str, check_downloading_files: bool = True
) -> bool:
    """
    Check if a file exists in the download directory or is currently being downloaded.

    Args:
        file_id (str): The ID of the file to check.
        file_name (str): The name of the file to check.
        check_downloading_files (bool): Whether to check the downloading_files dictionary.

    Returns:
        bool: True if the file exists

    Raises:
        Exception: If the file already exists in the download directory or is being downloaded.
    """
    if os.path.exists(os.path.join(DOWNLOAD_TO_DIR, file_name)):
        raise Exception("File already exists in downloads folder.")

    if check_downloading_files:
        if file_id in downloading_files:
            raise Exception("File is already being downloaded.")

        # Check file_name in downloading_files
        if any(file.file_name == file_name for file in downloading_files.values()):
            raise Exception("File is already being downloaded.")

    return True
