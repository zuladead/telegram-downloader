"""One bounded, batched sender; notification failures never fail downloads."""

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

from .safe_logging import safe_error

logger = logging.getLogger(__name__)


class Notifications:
    def __init__(self, bot, interval=5, min_interval=3.1):
        self.bot, self.interval, self.min_interval = bot, interval, min_interval
        self.pending = defaultdict(lambda: deque(maxlen=500))
        self.task = None
        self.next_send = 0
        self.retry_at = {}
        self.failures = defaultdict(int)

    def add(self, chat_id, text):
        # Cap each line so an individual long filename cannot exceed Telegram's limit.
        self.pending[chat_id].append(str(text)[:1000])

    def start(self):
        self.task = asyncio.create_task(self._run())

    async def flush(self):
        for chat_id in list(self.pending):
            lines = self.pending[chat_id]
            if not lines or self.retry_at.get(chat_id, 0) > time.monotonic():
                continue
            batch, length = [], 0
            for line in lines:
                units = len(line.encode('utf-16-le')) // 2
                if length + units + 1 > 3500:
                    break
                batch.append(line)
                length += units + 1
            await asyncio.sleep(max(0, self.next_send - time.monotonic()))
            try:
                await self.bot.send_message(chat_id=chat_id, text='\n'.join(batch))
            except Exception as exc:
                delay = getattr(exc, 'retry_after', None)
                if isinstance(delay, timedelta):
                    delay = delay.total_seconds()
                self.failures[chat_id] += 1
                delay = float(delay) + 1 if delay is not None else min(
                    60, 5 * 2 ** min(self.failures[chat_id], 4)
                )
                self.retry_at[chat_id] = time.monotonic() + delay
                # Flood control applies to all messages from this sender.
                self.next_send = max(self.next_send, time.monotonic() + delay)
                logger.warning("notification_failed chat=%s retry_in=%.1f error=%s",
                               chat_id, delay, safe_error(exc))
            else:
                # New events may arrive while the HTTP request is in flight.
                # Remove only the exact sent prefix; a full bounded deque may
                # have evicted that prefix, in which case retain newer events.
                for line in batch:
                    if lines and lines[0] == line:
                        lines.popleft()
                self.failures[chat_id] = 0
                self.retry_at.pop(chat_id, None)
                self.next_send = time.monotonic() + self.min_interval

    async def _run(self):
        while True:
            await asyncio.sleep(self.interval)
            await self.flush()

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
