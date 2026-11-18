import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import RetryAfter

from src.notifications import Notifications
from src.safe_logging import RedactingFormatter, safe_error


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_is_batched_into_one_message(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        notices = Notifications(bot)
        for i in range(60):
            notices.add(1, f'queued #{i}')
        await notices.flush()
        bot.send_message.assert_awaited_once()
        self.assertEqual(len(bot.send_message.call_args.kwargs['text'].splitlines()), 60)
        self.assertFalse(notices.pending[1])

    async def test_flood_wait_preserves_batch_and_prevents_immediate_resend(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RetryAfter(3)))
        notices = Notifications(bot)
        notices.add(1, 'success #1')
        await notices.flush()
        self.assertEqual(list(notices.pending[1]), ['success #1'])
        await notices.flush()
        self.assertEqual(bot.send_message.await_count, 1)
        bot.send_message.side_effect = None
        notices.retry_at[1] = notices.next_send = 0
        await notices.flush()
        self.assertFalse(notices.pending[1])

    async def test_messages_arriving_during_send_are_not_lost(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        notices = Notifications(bot)

        async def send(**_):
            notices.add(1, 'new event')

        bot.send_message.side_effect = send
        notices.add(1, 'old event')
        await notices.flush()
        self.assertEqual(list(notices.pending[1]), ['new event'])

    async def test_transport_failure_is_contained(self):
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
        notices = Notifications(bot)
        notices.add(1, 'success #1')
        await notices.flush()
        self.assertEqual(list(notices.pending[1]), ['success #1'])

    async def test_successful_messages_are_spaced(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        notices = Notifications(bot, min_interval=3.1)
        with patch('src.notifications.time.monotonic', return_value=100), patch(
            'src.notifications.asyncio.sleep', new_callable=AsyncMock
        ) as sleep:
            notices.add(1, 'first')
            await notices.flush()
            notices.add(1, 'second')
            await notices.flush()
        self.assertAlmostEqual(sleep.call_args.args[0], 3.1)

    async def test_emoji_batches_stay_below_telegram_text_limit(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        notices = Notifications(bot)
        for _ in range(10):
            notices.add(1, '😀' * 200)
        await notices.flush()
        text = bot.send_message.call_args.kwargs['text']
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 3500)
        self.assertTrue(notices.pending[1])


class RedactionTests(unittest.TestCase):
    def test_bot_token_is_removed_from_errors_and_tracebacks(self):
        import logging
        token = '123456789:abcdefghijklmnopqrstuvwxyz0123456789'
        self.assertNotIn(token, safe_error(OSError('/cache/' + token + '/file')))
        try:
            raise OSError(token)
        except OSError:
            import sys
            record = logging.LogRecord('test', 40, '', 1, token, (), sys.exc_info())
        self.assertNotIn(token, RedactingFormatter().format(record))
