import unittest
from types import SimpleNamespace

from src.cogs.downloader import get_message_file


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


if __name__ == "__main__":
    unittest.main()
