import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Message, MessageOriginChannel

from src.cogs.downloader import run_download, start_download
from src.task_queue import DownloadQueue
from src.models import downloading_files
from src.utils.file_naming import (
    MAX_FILENAME_BYTES, MAX_SOURCE_DIR_BYTES, build_download_name,
    build_source_directory, download_relative_path,
)

RECEIVED = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def media(unique_id="AgAD_abc-123", name="视频.mp4"):
    return SimpleNamespace(
        file_unique_id=unique_id,
        file_id="download-" + unique_id,
        file_name=name,
        file_size=5,
    )


def message(file=None, origin=None, caption="海边日落", date=RECEIVED):
    return SimpleNamespace(
        document=None,
        video=file or media(),
        audio=None,
        forward_origin=origin,
        caption=caption,
        date=date,
        reply_text=AsyncMock(),
        chat_id=1,
        message_id=1,
    )


def channel(title="示例频道", date=RECEIVED):
    return SimpleNamespace(type="channel", chat=SimpleNamespace(title=title), date=date)


class FilenameTests(unittest.TestCase):
    def test_requested_format(self):
        msg = message(origin=channel())
        self.assertEqual(
            build_download_name(msg, msg.video, "视频.mp4"),
            "海边日落_视频_2026-11-02_AgAD_abc-123.mp4",
        )

    def test_forward_date_takes_precedence_and_uses_beijing_date(self):
        origin = channel(date=datetime(2026, 10, 31, 17, 0, tzinfo=UTC))
        msg = message(origin=origin)
        self.assertIn("_2026-11-01_", build_download_name(msg, msg.video, "视频.mp4"))

    def test_direct_upload_uses_received_date_and_defaults(self):
        msg = message(caption=None, date=datetime(2026, 11, 1, 17, tzinfo=UTC))
        self.assertEqual(
            build_download_name(msg, msg.video, "视频.mp4"),
            "无说明_视频_2026-11-02_AgAD_abc-123.mp4",
        )

    def test_hidden_user_source(self):
        origin = SimpleNamespace(sender_user_name="匿名用户", date=RECEIVED)
        msg = message(origin=origin)
        self.assertEqual(build_source_directory(msg), "匿名用户")

    def test_chat_and_user_sources(self):
        for origin in (
            SimpleNamespace(sender_chat=SimpleNamespace(title="群组"), date=RECEIVED),
            SimpleNamespace(
                sender_user=SimpleNamespace(full_name="用户"), date=RECEIVED
            ),
        ):
            with self.subTest(origin=origin):
                msg = message(origin=origin)
                self.assertNotEqual(build_source_directory(msg), "来源未知")

    def test_different_files_with_same_original_name_have_different_names(self):
        first, second = message(file=media("unique-1")), message(file=media("unique-2"))
        self.assertNotEqual(
            build_download_name(first, first.video, "视频.mp4"),
            build_download_name(second, second.video, "视频.mp4"),
        )

    def test_repeated_forward_keeps_name_when_metadata_is_unchanged(self):
        first = message(origin=channel())
        later = message(origin=channel(), date=datetime(2026, 11, 5, tzinfo=UTC))
        self.assertEqual(
            build_download_name(first, first.video, "视频.mp4"),
            build_download_name(later, later.video, "视频.mp4"),
        )

    def test_extension_and_multi_dot_stem_are_preserved(self):
        msg = message()
        self.assertTrue(
            build_download_name(msg, msg.video, "film.part1.MKV").startswith(
                "海边日落_film.part1_"
            )
        )
        self.assertTrue(
            build_download_name(msg, msg.video, "film.part1.MKV").endswith(".MKV")
        )

    def test_document_without_extension(self):
        msg = message()
        self.assertTrue(
            build_download_name(msg, msg.video, "README").endswith("_AgAD_abc-123")
        )

    def test_invalid_characters_and_traversal_are_cleaned(self):
        msg = message(
            origin=channel("../坏:频道"), caption='行一\n行二\t<>:"/\\|?*\x00'
        )
        name = build_download_name(msg, msg.video, "../../视频.mp4")
        self.assertFalse(any(char in name for char in '<>:"/\\|?*\n\t\x00'))
        self.assertEqual(os.path.basename(name), name)
        folder = build_source_directory(msg)
        self.assertFalse(any(char in folder for char in '< > : " / \\ | ? *'.replace(" ", "")))
        self.assertEqual(os.path.basename(folder), folder)

    def test_empty_cleaned_values_use_defaults(self):
        msg = message(origin=channel(" \n "), caption="\n\t")
        name = build_download_name(msg, msg.video, "视频.mp4")
        self.assertEqual(build_source_directory(msg), "来源未知")
        self.assertTrue(name.startswith("无说明_"))

    def test_long_unicode_name_preserves_full_id_and_extension(self):
        msg = message(
            file=media("AgAD_" + "a" * 60),
            origin=channel("频道" * 100),
            caption="说明😀" * 100,
        )
        name = build_download_name(msg, msg.video, "原视频" * 100 + ".mp4")
        self.assertLessEqual(len(name.encode("utf-8")), MAX_FILENAME_BYTES)
        self.assertTrue(name.endswith("_" + msg.video.file_unique_id + ".mp4"))
        self.assertEqual(name.encode("utf-8").decode("utf-8"), name)
        folder = build_source_directory(msg)
        self.assertLessEqual(len(folder.encode("utf-8")), MAX_SOURCE_DIR_BYTES)
        self.assertEqual(folder.encode("utf-8").decode("utf-8"), folder)

    def test_caption_is_shortened_before_original_stem(self):
        msg = message(caption="说明" * 200)
        name = build_download_name(msg, msg.video, "完整原文件名.mp4")
        self.assertTrue(name.endswith("_完整原文件名_2026-11-02_AgAD_abc-123.mp4"))
        self.assertLess(len(name.split("_完整原文件名_")[0]), 400)
        self.assertLessEqual(len(name.encode("utf-8")), MAX_FILENAME_BYTES)

    def test_actual_telegram_message_origin(self):
        msg = Message.de_json(
            {
                "message_id": 1,
                "date": int(RECEIVED.timestamp()),
                "chat": {"id": 1, "type": "private", "first_name": "User"},
                "caption": "说明",
                "forward_origin": {
                    "type": "channel",
                    "date": int(RECEIVED.timestamp()),
                    "chat": {"id": -100123, "type": "channel", "title": "真实结构频道"},
                    "message_id": 7,
                },
                "video": {
                    "file_id": "download",
                    "file_unique_id": "actual-id",
                    "width": 100,
                    "height": 100,
                    "duration": 1,
                    "file_name": "视频.mp4",
                },
            },
            None,
        )
        self.assertIsInstance(msg.forward_origin, MessageOriginChannel)
        self.assertEqual(
            build_download_name(msg, msg.video, msg.video.file_name),
            "说明_视频_2026-11-02_actual-id.mp4",
        )
        self.assertEqual(build_source_directory(msg), "真实结构频道")


class DownloadNamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        downloading_files.clear()

    async def download_queued(self, msg, directory):
        import asyncio
        from unittest.mock import Mock
        notices = SimpleNamespace(add=Mock())
        manager = DownloadQueue(
            str(Path(directory) / 'state' / 'tasks.sqlite3'),
            lambda task_id, payload: run_download(None, task_id, payload), notices.add,
        )
        context = SimpleNamespace(application=SimpleNamespace(bot_data={
            'downloads': manager, 'notifications': notices,
        }))
        await manager.start()
        try:
            # Notification transport is deliberately unavailable. Admission and
            # worker completion must never call the old reply_text path.
            msg.reply_text.side_effect = TimeoutError('notification offline')
            await start_download(msg, context)
            await asyncio.wait_for(manager.queue.join(), 2)
            self.assertGreaterEqual(manager.summary().get('succeeded', 0), 1)
            self.assertEqual(set(manager.summary()), {'succeeded'})
            msg.reply_text.assert_not_awaited()
            return notices
        finally:
            await manager.close()

    async def test_same_original_names_are_saved_as_separate_files(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in (1, 2):
                source = Path(directory) / ("temporary-" + str(index))
                source.write_bytes(b"video")
                msg = message(file=media("id-" + str(index)), origin=channel())
                with (
                    patch("src.cogs.downloader.DOWNLOAD_TO_DIR", directory),
                    patch("src.utils.get_file.DOWNLOAD_TO_DIR", directory),
                    patch(
                        "src.cogs.downloader.get_file",
                        AsyncMock(return_value=SimpleNamespace(file_path=str(source))),
                    ),
                ):
                    await self.download_queued(msg, directory)
                target = Path(directory) / build_source_directory(msg) / build_download_name(
                    msg, msg.video, "视频.mp4"
                )
                self.assertEqual(target.read_bytes(), b"video")
                paths.append(target)
            self.assertNotEqual(paths[0], paths[1])
            self.assertEqual(len(list(Path(directory).glob('示例频道/*.mp4'))), 2)

    async def test_forward_origin_folders_and_unknown_source(self):
        with tempfile.TemporaryDirectory() as directory:
            for index, (origin, folder) in enumerate((
                (channel("频道甲"), "频道甲"),
                (SimpleNamespace(sender_user=SimpleNamespace(full_name="联系人乙"),
                                 date=RECEIVED), "联系人乙"),
                (None, "来源未知"),
            )):
                msg = message(file=media("id-" + str(index)), origin=origin)
                source = Path(directory) / f"temporary-{index}"
                source.write_bytes(b"video")
                with (
                    patch("src.cogs.downloader.DOWNLOAD_TO_DIR", directory),
                    patch(
                        "src.cogs.downloader.get_file",
                        AsyncMock(return_value=SimpleNamespace(file_path=str(source))),
                    ),
                ):
                    await self.download_queued(msg, directory)
                target = Path(directory) / folder / build_download_name(
                    msg, msg.video, "视频.mp4"
                )
                self.assertEqual(target.read_bytes(), b"video")
            self.assertFalse(list(Path(directory).glob("*.mp4")))

    async def test_legacy_queued_payload_still_saves_to_download_root(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "temporary"
            source.write_bytes(b"video")
            payload = {"file_id": "old-id", "file_name": "legacy.mp4", "file_size": 5}
            self.assertEqual(download_relative_path(payload), "legacy.mp4")
            with (
                patch("src.cogs.downloader.DOWNLOAD_TO_DIR", directory),
                patch(
                    "src.cogs.downloader.get_file",
                    AsyncMock(return_value=SimpleNamespace(file_path=str(source))),
                ),
            ):
                target = await run_download(None, "legacy-task", payload)
            self.assertEqual(Path(target).read_bytes(), b"video")
            self.assertEqual(Path(target), Path(directory) / "legacy.mp4")

    async def test_backticks_in_caption_are_preserved_in_plain_text_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "temporary"
            source.write_bytes(b"video")
            msg = message(caption="a`b")
            with (
                patch("src.cogs.downloader.DOWNLOAD_TO_DIR", directory),
                patch("src.utils.get_file.DOWNLOAD_TO_DIR", directory),
                patch(
                    "src.cogs.downloader.get_file",
                    AsyncMock(return_value=SimpleNamespace(file_path=str(source))),
                ),
            ):
                notices = await self.download_queued(msg, directory)
            response = notices.add.call_args.args[1]
            self.assertIn('a`b', response)
            self.assertIn('成功', response)


if __name__ == "__main__":
    unittest.main()
