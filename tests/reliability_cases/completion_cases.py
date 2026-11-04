"""Offline candidate contracts: every case imports normally in a disposable HOME."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
import sys
PYTHON = sys.executable
PRELUDE = '''
import socket
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network forbidden'))
import json, time, threading
from types import SimpleNamespace
from zeline import agent, config
from zeline.agent import Zeline
config.FALLBACK_MODEL = ''
config.FALLBACK_MODELS = ()
a = Zeline.__new__(Zeline)
a.api_key = 'fake'; a.base_url = 'https://offline.invalid'; a.model = 'fake'
a.protocol = 'openai'; a.messages = [{'role':'system','content':'test'}, {'role':'user','content':'work'}]
a._turn_memory_context = ''; a._turn_skill_context = ''; a.identity = 'offline'
a._record_usage = lambda p: None
a._streaming_enabled = lambda: False
a.executor = SimpleNamespace(schemas=[], _should_stop=None, _skill_telemetry=None)
a._trim_history = lambda: None
a._turn_cloudflare_detected = False
a.last_turn_tool_calls = 0; a._tool_calls_since_reflection = 0
class Response:
    status_code = 200
    ok = True
    def __init__(self, payload=None, lines=()):
        self.text = json.dumps(payload); self.lines = lines
    def close(self): pass
    def iter_lines(self, **kw): yield from self.lines

def post(payload):
    agent.requests.post = lambda *args, **kw: Response(payload)
'''

class CompletionTests(unittest.TestCase):
    def run_case(self, body):
        with tempfile.TemporaryDirectory(prefix='zeline-response-test-') as home:
            env = dict(os.environ, HOME=home, ZELINE_HOME=home+'/.zeline', PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([PYTHON, '-B', '-c', PRELUDE+'\n'+body], cwd=home, env=env, capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_nonstream_finish_metadata(self):
        self.run_case('''
post({'choices':[{'message':{'role':'assistant','content':'partial','reasoning_content':'SECRET'},'finish_reason':'length'}]})
m = a._call_llm()
assert m.get('finish_reason') == 'length', m
assert 'reasoning_content' not in m, m
a.protocol = 'anthropic'
post({'content':[{'type':'text','text':'partial'},{'type':'thinking','thinking':'SECRET'}], 'stop_reason':'max_tokens'})
m = a._call_llm()
assert m.get('stop_reason') == 'max_tokens', m
assert 'SECRET' not in str(m), m
''')

    def test_stream_completion_and_incomplete_eof_metadata(self):
        self.run_case('''
for reason,terminal,expected in [('stop', True, 'stop'), ('length', True, 'length'), (None, False, 'incomplete')]:
    lines = ['data: '+json.dumps({'choices':[{'delta':{'content':'Visible text'}}]})]
    if reason: lines.append('data: '+json.dumps({'choices':[{'delta':{},'finish_reason':reason}]}))
    if terminal: lines.append('data: [DONE]')
    m = a._consume_openai_stream(Response(lines=lines))
    assert m.get('finish_reason') == expected, m
lines = ['data: '+json.dumps({'type':'content_block_delta','delta':{'type':'text_delta','text':'partial'}}),
    'data: '+json.dumps({'type':'message_delta','delta':{'stop_reason':'max_tokens'}}),
    'data: '+json.dumps({'type':'message_stop'})]
m = a._consume_anthropic_stream(Response(lines=lines))
assert m.get('stop_reason') == 'max_tokens', m
m = a._consume_anthropic_stream(Response(lines=lines[:1]))
assert m.get('stop_reason') == 'incomplete', m
''')

    def test_absolute_deadline_stops_silent_io_and_dripping_stream(self):
        self.run_case('''
a._provider_deadline = time.monotonic()+0.08
started = time.monotonic()
try:
    a._wait_io(lambda: time.sleep(1))
    raise AssertionError('silent provider I/O exceeded the turn deadline')
except agent.ZelineError as exc:
    assert 'deadline' in str(exc).lower(), str(exc)
assert time.monotonic()-started < 0.5
class Drip(Response):
    def iter_lines(self, **kw):
        for _ in range(30):
            time.sleep(0.04)
            yield 'data: '+json.dumps({'choices':[{'delta':{'content':'x'}}]})
a._provider_deadline = time.monotonic()+0.1
started = time.monotonic()
try:
    a._consume_openai_stream(Drip())
    raise AssertionError('dripping stream exceeded the absolute deadline')
except agent.ZelineError as exc:
    assert 'deadline' in str(exc).lower()
assert time.monotonic()-started < 0.5
''')

if __name__ == '__main__':
    unittest.main(verbosity=2)
