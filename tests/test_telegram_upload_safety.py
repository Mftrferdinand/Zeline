"""File sends must not replay an ambiguous upload as a second method."""
from pathlib import Path
import tempfile
from unittest import TestCase, mock
import requests
from zeline.gateways import telegram


class TelegramUploadSafetyTests(TestCase):
    def test_timeout_does_not_replay_upload_via_document_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'fixture.png'
            path.write_bytes(b'fixture')
            with mock.patch.object(telegram._HTTP, 'post', side_effect=requests.ReadTimeout) as post:
                self.assertFalse(telegram._send_produced_file('fixture', 7, path, kind='image'))
            self.assertEqual(post.call_count, 1)

    def test_server_error_does_not_replay_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'fixture.png'
            path.write_bytes(b'fixture')
            response = mock.Mock(ok=False, status_code=500)
            response.json.return_value = {'ok': False, 'error_code': 500}
            with mock.patch.object(telegram._HTTP, 'post', return_value=response) as post:
                self.assertFalse(telegram._send_produced_file('fixture', 7, path, kind='image'))
            self.assertEqual(post.call_count, 1)
