"""HTML rejection fallback shares the original absolute transport deadline."""
import importlib
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class TelegramHtmlDeadlineTests(unittest.TestCase):
    def test_plain_fallback_cannot_outlive_the_original_deadline(self):
        telegram = importlib.import_module('zeline.gateways.telegram')
        released = threading.Event()
        rejection = SimpleNamespace(ok=False, json=lambda: {
            'ok': False, 'error_code': 400, 'description': "can't parse entities"},
            close=lambda: None)

        def delayed_json():
            released.wait(.4)
            return {'ok': True, 'result': {'message_id': 99}}

        delayed = SimpleNamespace(ok=True, json=delayed_json, close=released.set)
        http = SimpleNamespace(post=Mock(side_effect=[rejection, delayed]))
        try:
            with patch.object(telegram, '_HTTP', http):
                started = time.monotonic()
                result = telegram._api_call('http://unused', 'sendMessage',
                    timeout=.06, attempts=1, delivery=True, text='<b>x', parse_mode='HTML')
                elapsed = time.monotonic() - started
            self.assertIsNone(result)
            self.assertLess(elapsed, .25)
            self.assertEqual(http.post.call_count, 2)
        finally:
            released.set()
