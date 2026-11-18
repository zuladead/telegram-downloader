import tempfile
import unittest
from pathlib import Path

from src.cogs.downloader import _publish


class PublicationTests(unittest.TestCase):
    def test_success_publishes_complete_file_and_removes_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'cache', Path(directory) / 'final.mp4'
            source.write_bytes(b'video')
            _publish(source, target, 5, 'task1')
            self.assertEqual(target.read_bytes(), b'video')
            self.assertFalse(source.exists())
            self.assertFalse((target.parent / '.task1.part').exists())

    def test_size_mismatch_never_publishes_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'cache', Path(directory) / 'final.mp4'
            source.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, 'size mismatch'):
                _publish(source, target, 5, 'task1')
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), b'bad')
            self.assertFalse((target.parent / '.task1.part').exists())

    def test_existing_target_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'cache', Path(directory) / 'final.mp4'
            source.write_bytes(b'video')
            target.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):
                _publish(source, target, 5, 'task1')
            self.assertEqual(target.read_bytes(), b'existing')
            self.assertTrue(source.exists())
