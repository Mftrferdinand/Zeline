"""Provider error inspection must remain bounded and must not eagerly drain streams."""
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


class ProviderErrorBodyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'HOME': self.tmp.name, 'ZELINE_HOME': self.tmp.name,
                                     'ZELINE_API_KEY': 'fixture', 'ZELINE_MODEL': 'fixture',
                                     'ZELINE_BASE_URL': 'http://127.0.0.1:1/v1'})
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.module = importlib.import_module('zeline.agent')
        self.module.config.STREAM_RESPONSES = True
        self.module.config.MAX_PROVIDER_REQUESTS_PER_TURN = 1
        self.module.config.FALLBACK_MODELS = ()
        self.module.config.FALLBACK_MODEL = ''
        self.agent = self.module.Zeline(workspace=self.tmp.name, tool_profile='safe')

    def test_streamed_quota_error_is_bounded_without_accessing_response_text(self):
        class Raw(io.BytesIO):
            bytes_read = 0
            def read(self, amount: int | None = -1, decode_content=False):
                result = super().read(amount)
                self.bytes_read += len(result)
                return result

        class Response:
            ok = False
            status_code = 429
            headers = {'Content-Encoding': 'identity'}
            encoding = 'utf-8'
            closed = False
            raw = Raw(json.dumps({'error': {'code': 'insufficient_quota'}}).encode())

            @property
            def text(self):
                raise AssertionError('Never eagerly materialize a streamed provider error')

            def close(self):
                self.closed = True

        response = Response()
        with patch.object(self.module.requests, 'post', return_value=response) as post:
            with self.assertRaises(self.module.ZelineError) as caught:
                self.agent.send('Read only fixture')
        self.assertEqual(post.call_count, 1)
        self.assertNotIn('LOCAL execution budget', str(caught.exception))
        self.assertTrue(response.closed)
        self.assertLessEqual(response.raw.bytes_read, 32769)


if __name__ == '__main__':
    unittest.main()
