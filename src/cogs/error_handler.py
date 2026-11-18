import logging

from ..safe_logging import safe_error

logger = logging.getLogger(__name__)


async def error_handler(update, context):
    # Do not recursively send several new messages while Telegram is rate limiting.
    logger.error('update_failed update=%s error=%s',
                 getattr(update, 'update_id', None), safe_error(context.error))
    notices = context.application.bot_data.get('notifications')
    chat = getattr(update, 'effective_chat', None)
    if notices and chat:
        notices.add(chat.id, f'处理请求失败: {safe_error(context.error)[:180]}')
