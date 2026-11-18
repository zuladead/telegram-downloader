import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .cogs import downloader_commands, error_handler, general_commands
from .utils import env
from .cogs.downloader import run_download
from .notifications import Notifications
from .task_queue import DownloadQueue

logger = logging.getLogger(__name__)


async def bad_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Raise an error to trigger the error handler."""
    await context.bot.wrong_method_name()  # type: ignore[attr-defined]


async def initialize(application):
    notices = Notifications(application.bot, env.NOTIFY_BATCH_SECONDS, env.NOTIFY_MIN_INTERVAL)
    application.bot_data['notifications'] = notices
    manager = DownloadQueue(
        env.TASK_DB_PATH,
        lambda task_id, payload: run_download(application.bot, task_id, payload),
        notices.add, env.DOWNLOAD_CONCURRENCY, env.QUEUE_CAPACITY,
    )
    application.bot_data['downloads'] = manager
    await manager.start()
    notices.start()


async def stop_workers(application):
    await application.bot_data['downloads'].close()
    await application.bot_data['notifications'].close()


def main() -> None:
    # Create the Application and pass it your bot's token.
    application = (
        Application.builder()
        .token(env.BOT_TOKEN)
        .concurrent_updates(False)
        .post_init(initialize)
        .post_stop(stop_workers)
        .local_mode(True)
        .base_url(f"{env.LOCAL_BOT_API_URL}/bot")
        .base_file_url(f"{env.LOCAL_BOT_API_URL}/file/bot")
        .build()
    )

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("bad_command", bad_command))
    application.add_handlers(general_commands)
    application.add_handlers(downloader_commands)

    # error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)
