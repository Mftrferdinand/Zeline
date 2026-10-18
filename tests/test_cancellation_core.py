"""Cancellation integration tests: local HTTP and fixture subprocesses only."""
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from zeline.agent import Zeline, CANCELLED_REPLY
from zeline import tools


class CancellationCoreTests(unittest.TestCase):
    def setUp(self):
        # Other framework suites reload modules under isolated homes. Bind the
        # tested class and exception to the current module generation together.
        import importlib
        global Zeline, CANCELLED_REPLY, tools
        module = importlib.import_module("zeline.agent")
        Zeline, CANCELLED_REPLY = module.Zeline, module.CANCELLED_REPLY
        tools = importlib.import_module("zeline.tools")

    def test_cancel_preheaders_and_nonstream_body(self):
        for body_stall in (False, True):
            with self.subTest(body_stall=body_stall):
                entered, release = threading.Event(), threading.Event()
                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        if body_stall:
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/json')
                            self.end_headers()
                            self.wfile.flush()
                        entered.set()
                        release.wait(3)
                        if not body_stall:
                            self.send_response(200)
                            self.end_headers()
                        try:
                            self.wfile.write(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'late'}}]}).encode())
                        except OSError:
                            pass
                    def log_message(self, *args):
                        pass
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                server.daemon_threads = True
                threading.Thread(target=server.serve_forever, daemon=True).start()
                agent = Zeline(identity='test:cancel', tool_profile='safe')
                agent.base_url = 'http://127.0.0.1:%s' % server.server_port
                agent.api_key, agent.model = 'fixture', 'fixture'
                agent.stream_responses = False
                stop = threading.Event()
                result = []
                worker = threading.Thread(target=lambda: result.append(agent.send('hello', should_stop=stop.is_set)), daemon=True)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    start = time.monotonic()
                    stop.set()
                    agent.force_cancel()
                    worker.join(.8)
                    self.assertEqual(result, [CANCELLED_REPLY])
                    self.assertLess(time.monotonic() - start, 1)
                    snapshot = list(agent.messages)
                    release.set()
                    time.sleep(.1)
                    self.assertEqual(agent.messages, snapshot)
                finally:
                    stop.set()
                    release.set()
                    server.shutdown()
                    server.server_close()

    def test_parallel_tools_never_launch_after_cancel_from_callback(self):
        agent = Zeline(tool_profile='safe')
        stop = threading.Event()
        calls = [{'id': str(i), 'type': 'function', 'function':
                  {'name': 'read_file', 'arguments': '{"path":"unused"}'}} for i in range(8)]
        with patch.object(agent, '_call_llm', return_value={'role': 'assistant', 'tool_calls': calls}), \
             patch.object(agent.executor, 'run') as run:
            result = agent.send('fixture', should_stop=stop.is_set,
                                on_tool=lambda *args: stop.set())
        self.assertEqual(result, CANCELLED_REPLY)
        run.assert_not_called()

    def test_cancelled_executor_checks_again_after_plugin_hook(self):
        from types import SimpleNamespace
        executor = tools.ToolExecutor('fixture:plugin', profile='safe')
        stop = threading.Event()
        executor._should_stop = stop.is_set
        class Plugins:
            def before(self, name, args):
                stop.set()
                return SimpleNamespace(blocked=False, args=args)
            def after(self, name, args, result):
                return result
        executor.plugins = Plugins()
        with patch.object(executor, '_dispatch') as dispatch:
            self.assertIn('CANCELLED', executor.run('read_file', {'path': 'fixture'}))
        dispatch.assert_not_called()

    def test_foreground_launch_cancel_registration_race(self):
        import sys
        stop = threading.Event()
        tracked = tools._fg_track
        captured = []
        def register(identity, process):
            captured.append(process)
            stop.set()
            tools.cancel_identity(identity)  # Snapshot misses this new process.
            tracked(identity, process)
        with tempfile.TemporaryDirectory() as tmp, patch.object(tools, '_fg_track', side_effect=register):
            start = time.monotonic()
            code, output, timeout = tools._run_tracked(
                [sys.executable, '-c', 'import time;time.sleep(20)'], shell=False,
                cwd=tmp, seconds=3, identity='fixture:race', should_stop=stop.is_set)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertIsNotNone(captured[0].poll())
        self.assertNotEqual(code, 0)
        self.assertFalse(timeout)

    def test_stop_kills_only_owned_background_and_child_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            before = set(tools._BG_JOBS)
            try:
                for identity in ('fixture:owner', 'fixture:owner::sub1', 'fixture:other'):
                    self.assertIn('started background job=', tools._run_shell(
                        'sleep 20', workspace, background=True, identity=identity))
                jobs = [job for key, job in tools._BG_JOBS.items() if key not in before]
                self.assertEqual(tools.cancel_identity('fixture:owner'), 2)
                for job in jobs:
                    if job.identity == 'fixture:other':
                        self.assertIsNone(job.process.poll())
                    else:
                        self.assertIsNotNone(job.process.poll())
            finally:
                for key in set(tools._BG_JOBS) - before:
                    job = tools._BG_JOBS.pop(key)
                    tools._terminate_group(job.process)
                    job.close_log()

    def test_stalled_sse_read_is_cancellable_without_close_deadlock(self):
        agent = Zeline(tool_profile='safe')
        stop, entered, release = threading.Event(), threading.Event(), threading.Event()
        class Response:
            def iter_lines(self, **kwargs):
                entered.set()
                release.wait(3)
                yield 'data: [DONE]'
            def close(self):
                release.wait(3)
        response = Response()
        result = []
        def consume():
            from zeline.agent import _TurnCancelled
            agent._should_stop = stop.is_set
            try:
                agent._consume_openai_stream(response, None)
            except _TurnCancelled:
                result.append('cancelled')
        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            agent._active_response = response
            start = time.monotonic()
            stop.set()
            agent.force_cancel()
            worker.join(.8)
            self.assertEqual(result, ['cancelled'])
            self.assertLess(time.monotonic() - start, 1)
        finally:
            release.set()
            worker.join(2)

    def test_soft_interrupt_preserves_foreground_as_managed_background_then_stop_kills_it(self):
        stop = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            before = set(tools._BG_JOBS)
            entered = threading.Event()
            tracked = tools._fg_track
            def register(identity, process):
                tracked(identity, process)
                entered.set()
            output = []
            with patch.object(tools, '_fg_track', side_effect=register):
                worker = threading.Thread(target=lambda: output.append(tools._run_shell(
                    f'"{sys.executable}" -c "import time; print(\'started\', flush=True); time.sleep(20)"',
                    Path(tmp), timeout=30, identity='fixture:soft',
                    should_stop=stop.is_set, interrupt_reason=lambda: 'interrupt')), daemon=True)
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    stop.set()
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    jobs = [job for key, job in tools._BG_JOBS.items() if key not in before]
                    self.assertEqual(len(jobs), 1)
                    self.assertIn(jobs[0].job_id, output[0])
                    self.assertIsNone(jobs[0].process.poll())
                    self.assertEqual(tools.cancel_identity('fixture:soft'), 1)
                    self.assertIsNotNone(jobs[0].process.poll())
                finally:
                    stop.set()
                    tools.cancel_identity('fixture:soft')
                    for key in set(tools._BG_JOBS) - before:
                        tools._BG_JOBS.pop(key).close_log()

    def test_question_cancellation_before_registration_and_during_renderer(self):
        from zeline import interaction
        stop = threading.Event()
        stop.set()
        self.assertIn('CANCELLED', interaction.ask('fixture:question', 'Never show', should_stop=stop.is_set))
        self.assertIsNone(interaction.pending('fixture:question'))
        stop.clear()
        entered, release = threading.Event(), threading.Event()
        def renderer(entry):
            entered.set()
            release.wait(3)
            return 'late answer'
        interaction.register_channel('fixture:question', renderer)
        result = []
        worker = threading.Thread(target=lambda: result.append(interaction.ask(
            'fixture:question', 'Waiting', should_stop=stop.is_set)), daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            stop.set()
            worker.join(.8)
            self.assertFalse(worker.is_alive())
            self.assertIn('CANCELLED', result[0])
            self.assertIsNone(interaction.pending('fixture:question'))
        finally:
            release.set()
            interaction.unregister_channel('fixture:question')
            worker.join(2)

    def test_completed_tool_results_remain_in_history_after_stop(self):
        agent = Zeline(tool_profile='safe')
        stop = threading.Event()
        calls = [{'id': str(i), 'type': 'function', 'function':
                  {'name': 'add_memory', 'arguments': '{"text":"fixture"}'}} for i in range(2)]
        def run(*args):
            stop.set()
            return 'saved actual fixture result'
        with patch.object(agent, '_call_llm', return_value={'role': 'assistant', 'tool_calls': calls}), \
             patch.object(agent.executor, 'run', side_effect=run) as execute:
            self.assertEqual(agent.send('fixture', should_stop=stop.is_set), CANCELLED_REPLY)
        self.assertEqual(execute.call_count, 1)
        results = {row['tool_call_id']: row['content'] for row in agent.messages if row.get('role') == 'tool'}
        self.assertEqual(results['0'], 'saved actual fixture result')
        self.assertIn('1', results)

    def test_child_agent_inherits_cancel_token_and_never_starts_after_stop(self):
        executor = tools.ToolExecutor('fixture:parent', profile='safe')
        stop = threading.Event()
        executor._should_stop = stop.is_set
        with patch('zeline.agent.Zeline') as child:
            child.return_value.send.return_value = 'child result'
            self.assertEqual(executor._spawn_subagent('brief', '', '1'), 'child result')
            predicate = child.return_value.send.call_args.kwargs['should_stop']
            self.assertFalse(predicate())
            stop.set()
            self.assertTrue(predicate())
            self.assertEqual(executor._spawn_subagent('must not start', '', '2'), CANCELLED_REPLY)
            self.assertEqual(child.call_count, 1)

    def test_force_cancel_does_not_wait_for_close(self):
        agent = Zeline(tool_profile='safe')
        release = threading.Event()
        class Response:
            def close(self):
                release.wait(2)
        agent._active_response = Response()
        start = time.monotonic()
        try:
            agent.force_cancel()
            self.assertLess(time.monotonic() - start, .2)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
