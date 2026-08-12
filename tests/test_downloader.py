import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.cogs.downloader import get_message_file, resolve_file_path


def telegram_file(file_name="example.bin", unique_id="unique"):
    return SimpleNamespace(file_name=file_name, file_unique_id=unique_id)


class GetMessageFileTests(unittest.TestCase):
    def test_document(self):
        document = telegram_file("archive.zip")
        result, name = get_message_file(
            SimpleNamespace(document=document, video=None, audio=None)
        )
        self.assertIs(result, document)
        self.assertEqual(name, "archive.zip")

    def test_video(self):
        video = telegram_file("clip.mp4")
        result, name = get_message_file(
            SimpleNamespace(document=None, video=video, audio=None)
        )
        self.assertIs(result, video)
        self.assertEqual(name, "clip.mp4")

    def test_audio(self):
        audio = telegram_file("track.flac")
        result, name = get_message_file(
            SimpleNamespace(document=None, video=None, audio=audio)
        )
        self.assertIs(result, audio)
        self.assertEqual(name, "track.flac")

    def test_missing_video_name_gets_stable_fallback(self):
        video = telegram_file(None, "video-id")
        _, name = get_message_file(
            SimpleNamespace(document=None, video=video, audio=None)
        )
        self.assertEqual(name, "video_video-id.mp4")

    def test_missing_audio_name_gets_stable_fallback(self):
        audio = telegram_file(None, "audio-id")
        _, name = get_message_file(
            SimpleNamespace(document=None, video=None, audio=audio)
        )
        self.assertEqual(name, "audio_audio-id.mp3")


class ResolveFilePathTests(unittest.TestCase):
    def test_local_mode_absolute_path_is_preserved(self):
        file_path = "/var/lib/telegram-bot-api/token/videos/file_1.mp4"
        self.assertEqual(resolve_file_path(file_path), file_path)

    def test_relative_path_uses_bot_api_directory(self):
        with (
            patch("src.cogs.downloader.BOT_API_DIR", "/var/lib/telegram-bot-api/"),
            patch("src.cogs.downloader.TOKEN_SUB_DIR", "token"),
        ):
            self.assertEqual(
                resolve_file_path("videos/file_1.mp4"),
                os.path.join(
                    "/var/lib/telegram-bot-api/", "token", "videos/file_1.mp4"
                ),
            )


if __name__ == "__main__":
    unittest.main()
