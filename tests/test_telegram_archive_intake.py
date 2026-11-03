"""Bounded Telegram archive intake regressions; no network or owner data."""
from __future__ import annotations

import importlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


class TelegramArchiveIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        env = patch.dict(os.environ, {'ZELINE_HOME': str(self.home / 'state'),
                                     'ZELINE_API_KEY': 'fixture', 'ZELINE_MODEL': 'fixture'})
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.telegram = importlib.import_module('zeline.gateways.telegram')

    @staticmethod
    def archive(files):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files:
                archive.writestr(name, content)
        return output.getvalue()

    def test_zip_admission_keeps_original_without_parsing_or_previewing_members(self):
        payload = self.archive([(f'project/src/file{i}.py', f'VALUE = {i}') for i in range(21000)])
        with patch.object(self.telegram, '_extract_document_text', side_effect=AssertionError('ZIP intake must not parse members')):
            prompt, error = self.telegram._prepare_document_prompt(
                'large-project.zip', payload, 'application/zip', 'Inspect the final source file', 'telegram:42')
        self.assertIsNone(error)
        saved = list((self.telegram.config.DATA_DIR / 'document-inbox').rglob('*.zip'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), payload)
        self.assertIn(str(saved[0]), prompt)
        self.assertIn('zipfile', prompt)
        self.assertIn('Inspect the final source file', prompt)
        self.assertNotIn('VALUE = ', prompt)
        self.assertLessEqual(len(prompt), 16000)
        with zipfile.ZipFile(saved[0]) as archive:
            self.assertEqual(archive.read('project/src/file20999.py'), b'VALUE = 20999')

    def test_saved_zip_is_readable_through_real_agent_tool_beyond_preview_limits(self):
        marker = 'FOUND_BEYOND_PREVIEW_AND_ENTRY_512'
        payload = self.archive([(f'src/filler{i}.py', '# filler') for i in range(700)] +
                               [('src/late.txt', 'a' * 20000 + marker)])
        prompt, error = self.telegram._prepare_document_prompt(
            'project.zip', payload, 'application/zip', 'Read the end of src/late.txt', 'telegram:42')
        self.assertIsNone(error)
        self.assertNotIn(marker, prompt)
        saved = next((self.telegram.config.DATA_DIR / 'document-inbox').rglob('*.zip'))
        tools = importlib.import_module('zeline.tools')
        executor = tools.ToolExecutor('telegram:42', 'full', self.home)
        code = (f'import zipfile\nwith zipfile.ZipFile({str(saved)!r}) as archive:\n'
                "    with archive.open('src/late.txt') as member:\n"
                "        member.seek(20000)\n        print(member.read(100).decode())")
        result = executor.run('execute_code', {'code': code})
        self.assertIn(marker, result)

    def test_zip_cache_failure_does_not_pretend_a_partial_preview_is_the_archive(self):
        module = importlib.import_module('zeline.archive_intake')
        with patch.object(module, 'cache_document', side_effect=OSError('fixture')):
            prompt, error = self.telegram._prepare_document_prompt(
                'project.zip', self.archive([('README.md', 'not saved')]),
                'application/zip', 'Inspect this project', 'telegram:42')
        self.assertEqual(prompt, '')
        self.assertIsNotNone(error)
        self.assertIn('original ZIP', error)

    def test_dependency_heavy_project_is_previewed_instead_of_rejected_at_512(self):
        files = [(f'project/node_modules/dependency/file{i}.js', 'vendored code') for i in range(600)]
        files += [('project/README.md', '# Project intent\nInstall using npm ci'),
                  ('project/package.json', '{"name":"project","scripts":{"start":"node index.mjs"}}')]
        text, error = self.telegram._extract_document_text('project.zip', self.archive(files))
        self.assertIsNone(error, error)
        self.assertIn('Project intent', text)
        self.assertIn('package.json', text)
        self.assertIn('602', text)
        self.assertIn('partial', text.lower())
        self.assertNotIn('vendored code', text)
        self.assertLessEqual(len(text), 16000)

    def test_javascript_and_typescript_entrypoints_are_read_as_code(self):
        for extension in ('js', 'mjs', 'cjs', 'jsx', 'ts', 'tsx', 'sol', 'sh'):
            with self.subTest(extension=extension):
                payload = self.archive([(f'project/index.{extension}', 'ENTRYPOINT_CONTENT')])
                text, error = self.telegram._extract_document_text('project.zip', payload)
                self.assertIsNone(error)
                self.assertIn('ENTRYPOINT_CONTENT', text)

    def test_oversized_index_is_deferred_before_zipfile_allocates_members(self):
        import struct
        module = importlib.import_module('zeline.archive_intake')
        payload = struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, 20001, 20001, 0, 0, 0)
        original = module.zipfile.ZipFile
        with patch.object(module.zipfile, 'ZipFile', wraps=original) as parser:
            text, error = self.telegram._extract_document_text('huge-index.zip', payload)
        self.assertIsNone(error)
        self.assertIn('deferred', text.lower())
        parser.assert_not_called()

    def test_document_dispatch_preserves_original_zip_and_exposes_private_path(self):
        import threading
        from types import SimpleNamespace
        payload = self.archive([('README.md', '# Original archive')])
        update = {'message': {'chat': {'id': 42}, 'message_id': 7,
                             'caption': 'Analyze this project',
                             'document': {'file_name': 'project.zip', 'file_id': 'fixture'}}}
        with patch.object(self.telegram, '_download_document', return_value=(payload, None)), \
             patch.object(self.telegram, '_start_agent_reply') as start:
            self.telegram._dispatch_update('fixture-api', 'fixture-token',
                SimpleNamespace(admit=lambda *a, **kw: None), update,
                allowed=[42], tool_profile='full', stop_event=threading.Event())
            prompt, error = start.call_args.kwargs['prepare']()
        self.assertIsNone(error)
        files = list((self.telegram.config.DATA_DIR / 'document-inbox').rglob('*.zip'))
        self.assertEqual(len(files), 1, 'Original upload was lost after preview generation')
        self.assertEqual(files[0].read_bytes(), payload)
        self.assertIn(str(files[0]), prompt)
        self.assertIn('Analyze this project', prompt)
        self.assertIn('untrusted', prompt.lower())
        self.assertLessEqual(len(prompt), 16000)
        if os.name != 'nt':
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(files[0].parent.stat().st_mode & 0o777, 0o700)

    def test_cache_does_not_write_while_another_connection_holds_cache_lock(self):
        import sqlite3
        module = importlib.import_module('zeline.archive_intake')
        root = self.home / 'private-cache'
        root.mkdir()
        blocker = sqlite3.connect(root / '.cache-lock.sqlite3')
        self.addCleanup(blocker.close)
        blocker.execute('CREATE TABLE guard (id INTEGER)')
        blocker.commit()
        blocker.execute('BEGIN IMMEDIATE')
        with self.assertRaises(OSError):
            module.cache_document(root, 'telegram:42', 'notes.txt', b'not committed')
        self.assertEqual(list(root.rglob('*.txt')), [])
        blocker.rollback()

    def test_cache_identity_directory_growth_is_bounded_even_after_failed_uploads(self):
        module = importlib.import_module('zeline.archive_intake')
        root = self.home / 'private-cache'
        root.mkdir()
        for index in range(3):
            (root / (str(index) * 64)).mkdir()
        with patch.object(module, 'CACHE_MAX_FILES', 3), self.assertRaises(ValueError):
            module.cache_document(root, 'telegram:new-owner', 'notes.txt', b'not saved')

    def test_download_streams_and_stops_before_materializing_oversized_response(self):
        class Response:
            ok = True
            headers = {}
            closed = False
            chunks_consumed = 0

            @property
            def content(self):
                raise AssertionError('The response must be streamed, never eagerly materialized')

            def iter_content(self, chunk_size):
                for chunk in (b'a' * 6, b'b' * 6, b'c' * 6):
                    self.chunks_consumed += 1
                    yield chunk

            def close(self):
                self.closed = True

        response = Response()
        with patch.object(self.telegram, 'TELEGRAM_TEXT_FILE_LIMIT', 10), \
             patch.object(self.telegram, '_api_call', return_value={'result': {'file_path': 'fixture'}}), \
             patch.object(self.telegram.requests, 'get', return_value=response) as download:
            data, error = self.telegram._download_document('fixture-api', 'fixture-token',
                                                          {'file_id': 'fixture', 'file_name': 'project.zip'})
        self.assertIsNone(data)
        self.assertIsNotNone(error)
        self.assertTrue(response.closed)
        self.assertEqual(response.chunks_consumed, 2)
        self.assertTrue(download.call_args.kwargs['stream'])

    def test_extensionless_readme_and_build_manifest_are_not_ignored(self):
        payload = self.archive([('README', 'EXTENSIONLESS_PROJECT_GUIDE'),
                                ('Dockerfile', 'FROM python:3.13-slim')])
        text, error = self.telegram._extract_document_text('project.zip', payload)
        self.assertIsNone(error)
        self.assertIn('EXTENSIONLESS_PROJECT_GUIDE', text)
        self.assertIn('FROM python:3.13-slim', text)


if __name__ == '__main__':
    unittest.main()
