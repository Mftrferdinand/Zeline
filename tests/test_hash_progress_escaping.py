"""Checksum progress must escape arguments before rendering Telegram HTML."""
from zeline.gateways.telegram import _tool_progress_text


def test_hash_progress_escapes_algorithm():
    result = _tool_progress_text('hash_check', {'path': 'a.txt', 'algorithm': '<i>sha256</i>'})
    assert '<i>' not in result
    assert '&lt;i&gt;' in result
