import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Message, MessageOriginChannel

from src.cogs.downloader import start_download
from src.models import downloading_files
from src.utils.file_naming import MAX_FILENAME_BYTES, build_download_name

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
    )


def channel(title="示例频道", date=RECEIVED):
    return SimpleNamespace(type="channel", chat=SimpleNamespace(title=title), date=date)


class FilenameTests(unittest.TestCase):
    def test_requested_format(self):
        msg = message(origin=channel())
        self.assertEqual(
            build_download_name(msg, msg.video, "视频.mp4"),
            "视频_示例频道_2026-11-02_海边日落_AgAD_abc-123.mp4",
        )

    def test_forward_date_takes_precedence_and_uses_beijing_date(self):
        origin = channel(date=datetime(2026, 10, 31, 17, 0, tzinfo=UTC))
        msg = message(origin=origin)
        self.assertIn("_2026-11-01_", build_download_name(msg, msg.video, "视频.mp4"))

    def test_direct_upload_uses_received_date_and_defaults(self):
        msg = message(caption=None, date=datetime(2026, 11, 1, 17, tzinfo=UTC))
        self.assertEqual(
            build_download_name(msg, msg.video, "视频.mp4"),
            "视频_来源未知_2026-11-02_无说明_AgAD_abc-123.mp4",
        )

    def test_hidden_user_source(self):
        origin = SimpleNamespace(sender_user_name="匿名用户", date=RECEIVED)
        msg = message(origin=origin)
        self.assertIn("_匿名用户_", build_download_name(msg, msg.video, "视频.mp4"))

    def test_chat_and_user_sources(self):
        for origin in (
            SimpleNamespace(sender_chat=SimpleNamespace(title="群组"), date=RECEIVED),
            SimpleNamespace(
                sender_user=SimpleNamespace(full_name="用户"), date=RECEIVED
            ),
        ):
            with self.subTest(origin=origin):
                msg = message(origin=origin)
                self.assertNotIn(
                    "_来源未知_", build_download_name(msg, msg.video, "视频.mp4")
                )

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
                "film.part1_"
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

    def test_empty_cleaned_values_use_defaults(self):
        msg = message(origin=channel(" \n "), caption="\n\t")
        name = build_download_name(msg, msg.video, "视频.mp4")
        self.assertIn("_来源未知_", name)
        self.assertIn("_无说明_", name)

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

    def test_caption_is_shortened_before_original_stem(self):
        msg = message(caption="说明" * 200)
        name = build_download_name(msg, msg.video, "完整原文件名.mp4")
        self.assertTrue(name.startswith("完整原文件名_来源未知_2026-11-02_"))

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
            "视频_真实结构频道_2026-11-02_说明_actual-id.mp4",
        )


class DownloadNamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        downloading_files.clear()

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
                    await start_download(msg, SimpleNamespace(bot=None))
                target = Path(directory) / build_download_name(
                    msg, msg.video, "视频.mp4"
                )
                self.assertEqual(target.read_bytes(), b"video")
                paths.append(target)
            self.assertNotEqual(paths[0], paths[1])
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    async def test_backticks_in_caption_are_escaped_in_success_message(self):
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
                await start_download(msg, SimpleNamespace(bot=None))
            response = msg.reply_text.call_args.args[0]
            self.assertIn("a\\`b", response)
            self.assertIn("successfully", response)


if __name__ == "__main__":
    unittest.main()
