"""Per-provider wait boundary, independent of tools and total turn duration."""
import unittest
from unittest import mock
import test_agent as fixtures


class ProviderWaitTests(unittest.TestCase):
    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def test_repeated_stall_returns_visible_incomplete_answer_without_user(self):
        import threading
        agent = self.agent_module.Zeline(tool_profile='safe')
        release = threading.Event()
        calls, replies = [], []
        def post(*args, **kwargs):
            calls.append(1)
            release.wait(5)
            return fixtures.FakeResponse({'choices':[{'message':{'content':'late'},'finish_reason':'stop'}]})
        with mock.patch.object(self.agent_module.config,'PROVIDER_STALL_SECONDS',0.15,create=True), mock.patch.object(self.agent_module.requests,'post',side_effect=post):
            t=threading.Thread(target=lambda: replies.append(agent.send('Reply briefly.')),daemon=True)
            t.start();t.join(2);finished=not t.is_alive();release.set();t.join(3)
        self.assertTrue(finished)
        self.assertEqual(len(calls),2)
        self.assertFalse(agent.last_turn_complete)
        self.assertIn('Task paused, not completed',replies[0])

    def test_anthropic_content_counts_as_progress_not_silence(self):
        from zeline import provider_wait
        agent=self.agent_module.Zeline(tool_profile='safe')
        clock=[0.0]
        def lines(response):
            clock[0]=0.1
            yield 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}'
            clock[0]=0.2
            self.assertFalse(provider_wait.expired(),'Real Anthropic text was ignored by the idle detector')
            yield 'data: {"type":"message_stop"}'
        with mock.patch.object(provider_wait.time,'monotonic',side_effect=lambda:clock[0]), mock.patch.object(self.agent_module.config,'PROVIDER_STALL_SECONDS',0.15,create=True), mock.patch.object(agent,'_stream_lines',side_effect=lines):
            consume=provider_wait.bounded(lambda self: self._consume_anthropic_stream(object()))
            self.assertEqual(consume(agent)['content'],'hello')

    def test_real_http_drip_recovers_without_a_new_user_message(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        release = threading.Event()
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                calls.append(1)
                first = len(calls) == 1
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                try:
                    if first:
                        self.wfile.write(b'{"choices":[')
                        self.wfile.flush()
                        while not release.wait(0.02):
                            self.wfile.write(b' ')
                            self.wfile.flush()
                    else:
                        payload={'choices':[{'message':{'role':'assistant','content':'Recovered over real HTTP.'},'finish_reason':'stop'}]}
                        self.wfile.write(json.dumps(payload).encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        agent=self.agent_module.Zeline(tool_profile='safe')
        agent.base_url=f'http://127.0.0.1:{server.server_port}'
        replies,errors=[],[]
        def send():
            try:replies.append(agent.send('Reply briefly.'))
            except BaseException as exc:errors.append(type(exc).__name__)
        try:
            with mock.patch.object(self.agent_module.config,'PROVIDER_STALL_SECONDS',0.15,create=True):
                worker=threading.Thread(target=send,daemon=True)
                worker.start();worker.join(2);finished=not worker.is_alive()
                release.set();worker.join(3)
            self.assertTrue(finished,'Native HTTP waited for an external wakeup')
            self.assertEqual(errors,[])
            self.assertEqual(len(calls),2)
            self.assertEqual(replies,['Recovered over real HTTP.'])
            self.assertTrue(agent.last_turn_complete)
        finally:
            release.set();server.shutdown();server.server_close();thread.join(2)
