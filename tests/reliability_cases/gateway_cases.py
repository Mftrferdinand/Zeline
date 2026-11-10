"""Actual gateway -> session -> agent -> real file tool with blocked UI I/O."""
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch


class GatewayToolNonblockingIntegration(unittest.TestCase):
    def test_blocked_narration_does_not_block_real_tool_execution(self):
        tmp = tempfile.mkdtemp(prefix='zeline-gateway-')
        try:
            with patch.dict(os.environ, {'HOME': tmp, 'USERPROFILE': tmp, 'ZELINE_HOME': tmp}):
                self._exercise_gateway(tmp)
        finally:
            # _exercise_gateway releases and joins every thread it owns before
            # returning, including on assertion failure. Never mask a live-worker
            # diagnostic with Windows' secondary SQLite unlink error.
            active = [t for t in threading.enumerate()
                      if t.name == 'fixture-gateway-worker' or
                      getattr(t, '_gateway_fixture_owned', False)]
            if active:
                print('Gateway fixture retained for live-thread diagnosis: ' + tmp,
                      file=sys.stderr, flush=True)
            else:
                shutil.rmtree(tmp)

    def _exercise_gateway(self, tmp):
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                del sys.modules[name]
        config = importlib.import_module('zeline.config')
        config.PERSIST_SESSIONS = False
        sessions_module = importlib.import_module('zeline.sessions')
        gateway = importlib.import_module('zeline.gateways.telegram')
        transport_module = importlib.import_module('zeline.telegram_delivery')
        store = sessions_module.SessionStore()
        identity = 'fixture:gateway-tool-acceptance'
        session = store.get_or_create(identity, 'workspace', tmp)
        self.assertEqual(Path.home().resolve(), Path(tmp).resolve())
        target = Path(tmp) / 'tool-ran.txt'
        entered = threading.Event()
        release = threading.Event()
        ready_or_failed = threading.Event()
        provider_ready = threading.Event()
        start_turn = threading.Event()
        finished = threading.Event()
        tool_finished = threading.Event()
        errors = []
        outcomes = []
        sent = []
        transports = []
        responses = iter([
            {'content': 'Writing the requested file now.', 'tool_calls': [
                {'id': 'only-write', 'type': 'function', 'function': {
                    'name': 'write_file', 'arguments': json.dumps({
                        'path': str(target), 'content': 'Actual tool executed.\n'})}}]},
            {'content': 'Verified: the requested file was written.', 'finish_reason': 'stop'},
        ])
        real_run = session.agent.executor.run
        real_transport_init = transport_module.TurnTransport.__init__
        real_thread_start = threading.Thread.start
        owned_threads = []

        def thread_start(thread):
            thread._gateway_fixture_owned = True
            owned_threads.append(thread)
            return real_thread_start(thread)

        def transport_init(transport, *args, **kwargs):
            real_transport_init(transport, *args, **kwargs)
            transport.worker._gateway_fixture_owned = True
            transports.append(transport)

        def diagnostics():
            frames = sys._current_frames()
            stacks = []
            for thread in list(owned_threads):
                frame = frames.get(thread.ident)
                stacks.append(thread.name + ':\n' + (
                    ''.join(traceback.format_stack(frame)) if frame else '<exited>'))
            return '\n'.join(errors + stacks)

        def call_llm(**kwargs):
            # Task routing, SQLite/ACL setup, recall and lazy imports are real,
            # but not the transport-blocking contract. Reach the provider seam
            # before starting its 3s/1s budget; do not warm up a tool or reply.
            if not provider_ready.is_set():
                provider_ready.set()
                ready_or_failed.set()
                if not start_turn.wait(15):
                    raise AssertionError('Measured turn was not started')
            return next(responses)

        def run(name, args, *a, **kw):
            # Force overlap: a fast file write must not pass before the delivery
            # thread has actually entered the blocked API call. A synchronous
            # narration regression still fails because dispatch never gets here.
            if not entered.wait(3):
                raise AssertionError('Tool dispatch never overlapped narration transport')
            result = real_run(name, args, *a, **kw)
            if name == 'write_file':
                tool_finished.set()
            return result

        def api(_api, method, **payload):
            if method == 'sendMessage':
                if not entered.is_set():
                    if 'Writing the requested file now.' not in str(payload.get('text', '')):
                        errors.append('First blocked message was not the requested narration')
                    entered.set()
                    if not release.wait(8):
                        errors.append('Test transport was not released')
                        raise AssertionError(errors[-1])
                sent.append(str(payload.get('text', '')))
                return {'ok': True, 'result': {'message_id': len(sent)}}
            return {'ok': True, 'result': True}

        def send():
            try:
                outcomes.append(gateway._send_agent_reply(
                    'fixture-api', store, chat_id=1, identity=identity,
                    text='Write the requested file now.', tool_profile='workspace'))
            except BaseException:
                errors.append(traceback.format_exc())
            finally:
                finished.set()
                ready_or_failed.set()

        with patch.object(gateway, '_api_call', side_effect=api), \
             patch.object(session.agent, '_call_llm', side_effect=call_llm), \
             patch.object(session.agent.executor, 'run', side_effect=run), \
             patch.object(transport_module.TurnTransport, '__init__', transport_init), \
             patch.object(threading.Thread, 'start', thread_start):
            worker = threading.Thread(target=send, daemon=True, name='fixture-gateway-worker')
            worker.start()
            try:
                self.assertTrue(ready_or_failed.wait(15), 'Gateway setup never became ready\n' + diagnostics())
                self.assertTrue(provider_ready.is_set() and not finished.is_set(),
                                'Gateway exited before provider readiness\n' + diagnostics())
                self.assertFalse(target.exists(), 'Tool executed during setup')
                start_turn.set()
                self.assertTrue(entered.wait(3), 'No actual narration transport call was observed\n' + diagnostics())
                self.assertTrue(tool_finished.wait(1),
                                'Actual file tool waited behind cosmetic/narration network I/O\n' + diagnostics())
                self.assertFalse(release.is_set())
                self.assertEqual(target.read_text(), 'Actual tool executed.\n')
            finally:
                start_turn.set()
                release.set()
                worker.join(5)
                if worker.is_alive():
                    errors.append('Gateway worker exceeded finalization budget\n' + diagnostics())
                    store.request_stop(identity)
                    worker.join(5)  # cancellation cleanup, not an extended success budget
                for transport in transports:
                    transport.close()
                    transport.worker.join(5)
                for thread in list(owned_threads):
                    thread.join(5)
                self.assertFalse(worker.is_alive(), 'Gateway worker did not finalize\n' + diagnostics())
                self.assertTrue(all(not t.is_alive() for t in owned_threads),
                                'Owned gateway thread did not finalize\n' + diagnostics())
        self.assertEqual(errors, [])
        self.assertEqual(target.read_text(), 'Actual tool executed.\n')
        self.assertEqual(outcomes, [True])
        self.assertEqual(sum('Verified:' in part for part in sent), 1, 'Final answer was lost or duplicated')


if __name__ == '__main__':
    unittest.main(verbosity=2)
