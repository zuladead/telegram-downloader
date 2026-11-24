"""Persistent FIFO admission and a fixed pool of download workers."""

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from .safe_logging import safe_error
from .utils.file_naming import download_relative_path

logger = logging.getLogger(__name__)


class DownloadQueue:
    def __init__(self, database, runner, notify, concurrency=2, capacity=1000):
        self.runner, self.notify = runner, notify
        self.concurrency, self.capacity = concurrency, capacity
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tasks "
            "(id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL, "
            "created REAL NOT NULL, updated REAL NOT NULL, runs INTEGER DEFAULT 0, "
            "error TEXT DEFAULT '', result TEXT DEFAULT '')"
        )
        self.db.commit()
        self.queue = asyncio.Queue(maxsize=capacity)
        self.workers = []
        self.accepting = True

    def _save_state(self, task_id, state, error='', result=''):
        self.db.execute(
            "UPDATE tasks SET state=?, updated=?, error=?, result=? WHERE id=?",
            (state, time.time(), error, result, task_id),
        )
        self.db.commit()

    async def start(self):
        # Recover accepted work before receiving new Telegram updates.
        self.db.execute("UPDATE tasks SET state='queued' WHERE state='running'")
        self.db.commit()
        recovered = self.db.execute(
            "SELECT id FROM tasks WHERE state='queued' ORDER BY created, rowid"
        ).fetchall()
        # Honor already accepted work even when the configured capacity shrinks.
        self.queue = asyncio.Queue(maxsize=max(self.capacity, len(recovered)))
        for (task_id,) in recovered:
            self.queue.put_nowait(task_id)
        self.workers = [asyncio.create_task(self._worker()) for _ in range(self.concurrency)]
        logger.info("queue_started workers=%s recovered=%s", self.concurrency, self.queue.qsize())

    def submit(self, payload):
        if not self.accepting:
            raise ValueError("Queue is stopping; please send the file after restart")
        for task_id, encoded in self.db.execute(
            "SELECT id, payload FROM tasks WHERE state IN ('queued', 'running')"
        ):
            existing = json.loads(encoded)
            if (existing['file_id'] == payload['file_id']
                    or download_relative_path(existing) == download_relative_path(payload)):
                return task_id, False
        if self._outstanding() >= self.capacity:
            raise ValueError("Queue is full; please resend this file later")
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        self.db.execute(
            "INSERT INTO tasks(id,payload,state,created,updated) VALUES(?,?,'queued',?,?)",
            (task_id, json.dumps(payload, ensure_ascii=False), now, now),
        )
        self.db.commit()
        self.queue.put_nowait(task_id)
        logger.info("task=%s state=queued message=%s bytes=%s", task_id,
                    payload.get('message_id'), payload['file_size'])
        return task_id, True

    def retry_failed(self, task_id=None):
        if not self.accepting:
            return 0
        query = "SELECT id,payload FROM tasks WHERE state='failed'"
        args = ()
        if task_id:
            query += " AND id=?"
            args = (task_id,)
        rows = self.db.execute(query + " ORDER BY created,rowid", args).fetchall()
        count = 0
        for job_id, encoded in rows:
            if self._outstanding() >= self.capacity:
                break
            payload = json.loads(encoded)
            # Do not retry beside an already active copy of the same media.
            active = self.db.execute(
                "SELECT payload FROM tasks WHERE state IN ('queued','running')"
            ).fetchall()
            if any(json.loads(p[0])['file_id'] == payload['file_id']
                   or download_relative_path(json.loads(p[0])) == download_relative_path(payload)
                   for p in active):
                continue
            self._save_state(job_id, 'queued')
            self.queue.put_nowait(job_id)
            logger.info("task=%s state=queued reason=manual_retry", job_id)
            count += 1
        return count

    def summary(self):
        return dict(self.db.execute("SELECT state,COUNT(*) FROM tasks GROUP BY state"))

    def _outstanding(self):
        return self.db.execute(
            "SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running')"
        ).fetchone()[0]

    def recent(self, state=None, limit=10):
        query = "SELECT id,state,payload,error,runs FROM tasks"
        args = ()
        if state:
            query += " WHERE state=?"
            args = (state,)
        return self.db.execute(query + " ORDER BY updated DESC LIMIT ?", (*args, limit)).fetchall()

    def _notify(self, chat_id, text):
        try:
            self.notify(chat_id, text)
        except Exception as exc:
            logger.warning('notification_enqueue_failed error=%s', safe_error(exc))

    async def _worker(self):
        while True:
            task_id = await self.queue.get()
            payload = json.loads(self.db.execute(
                "SELECT payload FROM tasks WHERE id=?", (task_id,)
            ).fetchone()[0])
            started = time.monotonic()
            try:
                self._save_state(task_id, 'running')
                self.db.execute("UPDATE tasks SET runs=runs+1 WHERE id=?", (task_id,))
                self.db.commit()
                logger.info("task=%s state=running", task_id)
                result = await self.runner(task_id, payload)
                self._save_state(task_id, 'succeeded', result=str(result))
                logger.info("task=%s state=succeeded elapsed=%.2f result=%s",
                            task_id, time.monotonic() - started, result)
                self._notify(payload['chat_id'], f"成功 #{task_id}: {download_relative_path(payload)}")
            except asyncio.CancelledError:
                self._save_state(task_id, 'queued', error='Interrupted; recovered on restart')
                logger.info("task=%s state=queued reason=shutdown", task_id)
                raise
            except Exception as exc:
                error = safe_error(exc)
                self._save_state(task_id, 'failed', error=error)
                logger.error("task=%s state=failed elapsed=%.2f error=%s",
                             task_id, time.monotonic() - started, error)
                self._notify(payload['chat_id'], f"失败 #{task_id}: {error[:180]}")
            finally:
                self.queue.task_done()

    async def close(self):
        self.accepting = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.db.close()
