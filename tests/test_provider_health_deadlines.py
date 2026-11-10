"""Optional provider telemetry must never perform a body read on the control plane."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zeline import provider_health


class ProviderHealthDeadlineTests(unittest.TestCase):
    def test_uncached_error_body_is_not_read(self):
        class StreamedResponse:
            _content = False
            raw_reads = 0

            @property
            def raw(self):
                self.raw_reads += 1
                raise AssertionError("Telemetry must not touch streaming I/O")

        response = StreamedResponse()
        self.assertIsNone(provider_health._error_payload(response))
        self.assertEqual(response.raw_reads, 0)

    def test_cached_small_structured_error_still_classifies_quota(self):
        events = []
        context = {'observer': events.append, 'http_status': None,
                   'category': 'error', 'failed': False}
        with patch.object(provider_health, '_CURRENT') as current:
            current.get.return_value = context
            provider_health.response_received(SimpleNamespace(
                status_code=429, _content=b'{"error":{"type":"insufficient_quota"}}'))
        self.assertEqual(events, [{'status': 'error', 'category': 'credit_exhausted',
                                   'http_status': 429}])

    def test_large_cached_error_is_not_parsed(self):
        self.assertIsNone(provider_health._error_payload(
            SimpleNamespace(_content=b'x' * 4097)))
