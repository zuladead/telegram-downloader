import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from src.task_queue import DownloadQueue


def payload(index):
    return dict(file_id=f'file-{index}', file_name=f'{index}.mp4',
                file_size=5, chat_id=1, message_id=index)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / 'tasks.sqlite3')
        self.managers = []

    async def asyncTearDown(self):
        for manager in self.managers:
            await manager.close()
        self.directory.cleanup()

    def manager(self, runner, **kwargs):
        manager = DownloadQueue(self.path, runner, lambda *_: None, **kwargs)
        self.managers.append(manager)
        return manager

    async def test_burst_of_60_respects_two_workers_and_finishes_after_failures(self):
        active = peak = 0
        started = []

        async def runner(task_id, data):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.append(data['message_id'])
            try:
                await asyncio.sleep(0)
                if data['message_id'] % 7 == 0:
                    raise OSError('network failure')
                return 'saved.mp4'
            finally:
                active -= 1

        manager = self.manager(runner)
        await manager.start()
        for index in range(60):
            manager.submit(payload(index))
        await asyncio.wait_for(manager.queue.join(), 5)
        self.assertEqual(peak, 2)
        self.assertEqual(started, list(range(60)))
        self.assertEqual(manager.summary(), {'failed': 9, 'succeeded': 51})

    async def test_capacity_rejects_without_saving_and_active_duplicate_is_idempotent(self):
        manager = self.manager(AsyncMock(), capacity=1)
        task_id, created = manager.submit(payload(1))
        self.assertTrue(created)
        self.assertEqual(manager.submit(payload(1)), (task_id, False))
        with self.assertRaisesRegex(ValueError, 'full'):
            manager.submit(payload(2))
        self.assertEqual(manager.summary(), {'queued': 1})

    async def test_same_filename_in_different_source_folders_is_not_duplicate(self):
        manager = self.manager(AsyncMock(), capacity=2)
        first = payload(1)
        first["source_dir"] = "频道甲"
        second = payload(2)
        second["file_name"] = first["file_name"]
        second["source_dir"] = "频道乙"
        one_id, one_created = manager.submit(first)
        two_id, two_created = manager.submit(second)
        self.assertTrue(one_created)
        self.assertTrue(two_created)
        self.assertNotEqual(one_id, two_id)

    async def test_failed_jobs_persist_and_manual_retry_does_not_duplicate(self):
        manager = self.manager(AsyncMock(side_effect=OSError('offline')))
        await manager.start()
        job, _ = manager.submit(payload(1))
        await asyncio.wait_for(manager.queue.join(), 2)
        await manager.close()
        self.managers.remove(manager)
        runner = AsyncMock(return_value='saved.mp4')
        manager = self.manager(runner)
        await manager.start()
        self.assertEqual(manager.summary(), {'failed': 1})
        self.assertEqual(manager.retry_failed(job), 1)
        self.assertEqual(manager.retry_failed(job), 0)
        await asyncio.wait_for(manager.queue.join(), 2)
        runner.assert_awaited_once()
        self.assertEqual(manager.recent()[0][4], 2)
        self.assertEqual(manager.summary(), {'succeeded': 1})

    async def test_shutdown_recovers_running_and_waiting_even_with_smaller_capacity(self):
        started = asyncio.Event()

        async def runner(*_):
            started.set()
            await asyncio.Event().wait()

        manager = self.manager(runner, concurrency=1)
        await manager.start()
        manager.submit(payload(1))
        manager.submit(payload(2))
        await asyncio.wait_for(started.wait(), 2)
        await manager.close()
        self.managers.remove(manager)
        runner = AsyncMock(return_value='saved.mp4')
        recovered = self.manager(runner, concurrency=1, capacity=1)
        await recovered.start()
        with self.assertRaisesRegex(ValueError, 'full'):
            recovered.submit(payload(3))
        await asyncio.wait_for(recovered.queue.join(), 2)
        self.assertEqual(runner.await_count, 2)

    async def test_retry_skips_an_active_copy(self):
        manager = self.manager(AsyncMock())
        job, _ = manager.submit(payload(1))
        manager._save_state(job, 'failed', error='offline')
        # The old queued ID is irrelevant here; this exercises admission only.
        manager.submit(payload(1))
        self.assertEqual(manager.retry_failed(), 0)

    async def test_notification_error_does_not_change_success_or_stop_worker(self):
        manager = self.manager(AsyncMock(return_value='saved.mp4'), concurrency=1)
        manager.notify = lambda *_: (_ for _ in ()).throw(TimeoutError('notification offline'))
        await manager.start()
        manager.submit(payload(1))
        manager.submit(payload(2))
        await asyncio.wait_for(manager.queue.join(), 2)
        self.assertEqual(manager.summary(), {'succeeded': 2})


if __name__ == '__main__':
    unittest.main()
