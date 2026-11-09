"""Media receipt names must not overwrite earlier uploads sharing a prefix."""
import tempfile
from pathlib import Path
from unittest import TestCase, mock
from zeline.gateways import telegram


class TelegramMediaStorageTests(TestCase):
    def test_common_file_id_prefix_does_not_overwrite_previous_upload(self):
        prefix = 'A' * 24
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(telegram, 'MEDIA_INBOX', Path(tmp)), \
                mock.patch.object(telegram, '_api_call', return_value={
                    'result': {'file_path': 'photos/file.jpg'}}), \
                mock.patch.object(telegram, '_download_attachment_bytes',
                                  side_effect=[(b'first image', None), (b'second image', None)]):
            first, error = telegram._download_media_file('fixture', 'fixture', prefix + 'one', '.jpg')
            self.assertIsNone(error)
            second, error = telegram._download_media_file('fixture', 'fixture', prefix + 'two', '.jpg')
            self.assertIsNone(error)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b'first image')
            self.assertEqual(second.read_bytes(), b'second image')
