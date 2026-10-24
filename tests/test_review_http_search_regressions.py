"""Offline reproductions from independent review, never real credentials."""
import io
import os
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from zeline import file_io


@pytest.mark.parametrize('target', ['content', 'files'])
def test_public_non_utf8_filename_survives_fallback(tmp_path, monkeypatch, target):
    if os.name == 'nt':
        pytest.skip('POSIX arbitrary-byte filename')
    try:
        (tmp_path / os.fsdecode(b'public\xff.txt')).write_text('NEEDLE\n')
    except OSError as exc:
        import errno
        if exc.errno == errno.EILSEQ:
            pytest.skip('filesystem rejects invalid UTF-8 filenames')
        raise
    monkeypatch.setattr(file_io.shutil, 'which', lambda *args: None)
    result = file_io.search_files('NEEDLE' if target == 'content' else 'public',
                                 tmp_path, target=target)
    assert not result.startswith('ERROR'), result
    assert 'public' in result


def registry_at(tmp_path):
    from zeline import openapi_tools
    (tmp_path / 'fixture.yaml').write_text('''openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example}]
paths: {/api: {get: {operationId: getFixture}}}
''')
    return openapi_tools.OpenApiRegistry('full', tmp_path)


@pytest.mark.parametrize('integration', ['native', 'openapi'])
@pytest.mark.parametrize('tombstone,expected_final', [
    ('sid=; Max-Age=0; Path=/', None),
    ('sid=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/', None),
    ('sid=; Max-Age=0; Path=/other', 'sid=OLD'),
    ('sid=; Max-Age=0; Domain=other.example; Path=/', 'sid=OLD'),
])
def test_redirect_cookie_tombstone_is_applied(tmp_path, integration, tombstone, expected_final):
    from zeline import tools, openapi_tools
    registry = registry_at(tmp_path)
    sent = []

    class Raw(io.BytesIO):
        def read(self, amount=-1, decode_content=False):
            return super().read(amount)
        def release_conn(self):
            pass

    def send(adapter, request, **kwargs):
        sent.append(request.headers.get('Cookie'))
        index = len(sent)
        headers = Message()
        result = requests.Response()
        result.request = request
        result.url = request.url
        result.encoding = 'utf-8'
        result.status_code = 302 if index < 3 else 200
        result.reason = 'OK'
        if index < 3:
            headers['Set-Cookie'] = ('sid=OLD; Path=/' if index == 1
                                     else tombstone)
            result.headers['Location'] = '/logout' if index == 1 else '/done'
            result.headers['Set-Cookie'] = headers['Set-Cookie']
        result.raw = Raw(b'done' if index == 3 else b'')
        result.raw._original_response = SimpleNamespace(msg=headers)
        # HTTPAdapter.build_response normally initializes response.cookies.
        requests.cookies.extract_cookies_to_jar(result.cookies, request, result.raw)
        return result

    with patch.object(requests.adapters.HTTPAdapter, 'send', send), \
            patch.object(tools, '_is_internal_ip', return_value=False), \
            patch.object(openapi_tools, '_is_internal_host', return_value=False):
        if integration == 'native':
            result = tools._http_request('GET', 'https://fixture.example/api')
        else:
            result = registry.call('api_fixture_getFixture', {})
    assert 'Status: 200' in result, result
    assert sent == [None, 'sid=OLD', expected_final], sent


def test_openapi_invalid_header_exception_does_not_expose_config_secret(tmp_path):
    from zeline import openapi_tools
    registry = registry_at(tmp_path)
    secret = 'FIXTURE_SECRET\nBAD'

    def security(tool, headers, params, cookies):
        headers['Authorization'] = 'Bearer ' + secret
        return [secret], None

    with patch.object(openapi_tools, '_is_internal_host', return_value=False), \
            patch.object(openapi_tools, '_apply_security', side_effect=security), \
            patch.object(requests.adapters.HTTPAdapter, 'send',
                         side_effect=AssertionError('network must not be reached')):
        result = registry.call('api_fixture_getFixture', {})
    assert result.startswith('ERROR OpenAPI request: InvalidHeader'), result
    assert 'FIXTURE_SECRET' not in result, result


def test_native_invalid_header_exception_omits_credentials():
    import json
    from zeline import tools
    with patch.object(tools, '_is_internal_ip', return_value=False), \
            patch.object(requests.adapters.HTTPAdapter, 'send',
                         side_effect=AssertionError('network must not be reached')):
        result = tools._http_request('GET', 'https://fixture.example/api',
                                     json.dumps({'Authorization': 'Bearer FIXTURE_NATIVE\nBAD'}))
    assert result.startswith('ERROR request: InvalidHeader'), result
    assert 'FIXTURE_NATIVE' not in result, result


def test_openapi_response_metadata_does_not_expose_literal_config_secret(tmp_path):
    from zeline import openapi_tools
    from tests.test_http_response_limits import bytes_response
    registry = registry_at(tmp_path)
    secret = 'FIXTURE_METADATA_SECRET'
    response = bytes_response(b'public', headers={'Content-Type': secret})
    response.reason = secret
    with patch.object(openapi_tools, '_is_internal_host', return_value=False), \
            patch.object(openapi_tools, '_apply_security', return_value=([secret], None)), \
            patch.object(openapi_tools.requests, 'request', return_value=response):
        result = registry.call('api_fixture_getFixture', {})
    assert secret not in result, result
    assert '[REDACTED]' in result
