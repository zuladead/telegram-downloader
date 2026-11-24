import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from telegram import Message, Update
from telegram.ext import ContextTypes, filters

from ..middlewares.auth import auth_required
from ..middlewares.handlers import callback_query_handler, command_handler, message_handler
from ..models import DownloadFile
from ..utils import env, get_file
from ..utils.file_naming import build_download_name, build_source_directory, download_relative_path

logger = logging.getLogger(__name__)
BOT_API_DIR = env.BOT_API_DIR
DOWNLOAD_TO_DIR = env.DOWNLOAD_TO_DIR
TOKEN_SUB_DIR = env.BOT_TOKEN.replace(':', '') if os.name == 'nt' else env.BOT_TOKEN


def get_message_file(message: Message):
    media = message.document or message.video or message.audio
    if media is None:
        raise ValueError('Message does not contain a supported file')
    name = media.file_name
    if not name:
        kind, extension = ('video', '.mp4') if message.video else ('audio', '.mp3')
        name = f'{kind}_{media.file_unique_id}{extension}'
    return media, name


def resolve_file_path(file_path):
    if os.path.isabs(file_path):
        return file_path
    return os.path.join(BOT_API_DIR, TOKEN_SUB_DIR, file_path.lstrip('/\\'))


def _publish(source, target, expected_size, task_id):
    """Copy to a task-specific partial file; only publish verified complete bytes."""
    partial = target.with_name(f'.{task_id}.part')
    try:
        shutil.copyfile(source, partial)
        size = partial.stat().st_size
        if expected_size and size != expected_size:
            raise ValueError(f'File size mismatch: expected {expected_size}, got {size}')
        # Never overwrite a file created while this task was downloading.
        # Hard-link publication is atomic and fails if the target exists.
        os.link(partial, target)
        partial.unlink()
        source.unlink()
    finally:
        partial.unlink(missing_ok=True)


async def run_download(bot, task_id, payload):
    relative_path = download_relative_path(payload)
    target = Path(DOWNLOAD_TO_DIR) / relative_path
    if target.exists():
        # A process may have stopped after publication but before saving success.
        if payload['file_size'] and target.stat().st_size == payload['file_size']:
            logger.info('task=%s state=verified_existing result=%s', task_id, target)
            return str(target)
        raise FileExistsError('Destination exists with unknown or mismatched size')
    file = DownloadFile(payload['file_id'], relative_path, payload['file_size'])
    file.task_id = task_id
    # Bound all getFile attempts together; local Bot API may still transfer in
    # the background after the HTTP request has ended.
    async with asyncio.timeout(env.DOWNLOAD_TASK_TIMEOUT):
        downloaded = await get_file(bot, file)
    if not downloaded.file_path:
        raise ValueError('Bot API returned no local file path')
    source = Path(resolve_file_path(downloaded.file_path))
    size = source.stat().st_size
    if payload['file_size'] and size != payload['file_size']:
        raise ValueError(f'File size mismatch: expected {payload["file_size"]}, got {size}')
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info('task=%s state=moving bytes=%s', task_id, size)
    # A copying thread cannot be cancelled. Keep its slot until it finishes so
    # restart/cancellation cannot race another writer or lose its final state.
    move = asyncio.create_task(asyncio.to_thread(
        _publish, source, target, payload['file_size'], task_id
    ))
    try:
        await asyncio.shield(move)
    except asyncio.CancelledError:
        await move
        raise
    if os.name != 'nt':
        os.chmod(target, 0o664)
    return str(target)


async def start_download(message, context):
    media, name = get_message_file(message)
    payload = {
        'file_id': media.file_id,
        'file_unique_id': media.file_unique_id,
        'file_name': build_download_name(message, media, name),
        'source_dir': build_source_directory(message),
        'file_size': media.file_size or 0,
        'chat_id': message.chat_id,
        'message_id': message.message_id,
    }
    manager = context.application.bot_data['downloads']
    notices = context.application.bot_data['notifications']
    try:
        task_id, created = manager.submit(payload)
    except ValueError as exc:
        logger.warning('state=rejected message=%s error=%s', message.message_id, exc)
        notices.add(message.chat_id, f'未入队 message={message.message_id}: {exc}')
        return
    verb = '已排队' if created else '已在队列中'
    notices.add(message.chat_id, f'{verb} #{task_id}: {download_relative_path(payload)}')


@command_handler('status')
@auth_required
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    manager = context.application.bot_data['downloads']
    counts = manager.summary()
    text = f'并发上限: {manager.concurrency}\n' + '\n'.join(
        f'{label}: {counts.get(state, 0)}' for state, label in [
            ('queued', '等待'), ('running', '下载/移动中'),
            ('succeeded', '成功'), ('failed', '失败')
        ]
    )
    for task_id, state, encoded, error, runs in manager.recent(state='failed', limit=5):
        name = download_relative_path(json.loads(encoded))[:40]
        text += f'\n#{task_id} {name}\n{state} 执行{runs}次 {error[:60]}'
    text += '\n/retry_failed 重试失败任务；/retry 任务编号 重试单个任务'
    context.application.bot_data['notifications'].add(update.effective_chat.id, text)


@command_handler(['retry_failed', 'retry'])
@auth_required
async def retry_failed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    manager = context.application.bot_data['downloads']
    notices = context.application.bot_data['notifications']
    command = update.effective_message.text.split()[0].split('@')[0]
    if command == '/retry' and len(context.args) != 1:
        notices.add(update.effective_chat.id, '用法: /retry 任务编号')
        return
    count = manager.retry_failed(context.args[0] if command == '/retry' else None)
    notices.add(update.effective_chat.id, f'已将 {count} 个失败任务重新排队。队列满时剩余任务保留失败状态。')


SUPPORTED_FILES = filters.Document.ALL | filters.VIDEO | filters.AUDIO


@message_handler(SUPPORTED_FILES & filters.ChatType.PRIVATE)
@auth_required
async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_download(update.message, context)


@callback_query_handler()
@auth_required
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == 'yes':
        await start_download(update.effective_message.reply_to_message, context)
    # A failed acknowledgement must not prevent already accepted work.
    try:
        await query.answer()
        await update.effective_message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.warning('legacy_button_ack_failed')
