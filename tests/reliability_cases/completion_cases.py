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
            env = dict(os.environ, HOME=home, USERPROFILE=home, ZELINE_HOME=home+'/.zeline', PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([PYTHON, '-B', '-c', PRELUDE+'\n'+body], cwd=home, env=env, capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_empty_nonstream_response_recovers_without_replaying_tools(self):
        self.run_case('''
a.last_turn_complete = True
a.messages += [{'role':'assistant','content':'','tool_calls':[{'id':'done','type':'function','function':{'name':'read_file','arguments':'{}'}}]}, {'role':'tool','tool_call_id':'done','content':'VERIFIED_RESULT'}]
before = json.loads(json.dumps(a.messages))
calls = []
def request(*args, **kwargs):
    calls.append(kwargs['json'])
    text = None if len(calls) == 1 else 'Verified final answer'
    return Response({'choices':[{'message':{'role':'assistant','content':text},'finish_reason':'stop'}]})
agent.requests.post = request
answer = a._run_turn()
assert answer == 'Verified final answer', answer
assert len(calls) == 2, calls
assert a.messages[:-1] == before, a.messages
assert a.last_turn_complete is True
assert all('VERIFIED_RESULT' in str(p['messages']) for p in calls)
''')

    def test_empty_response_exhaustion_is_incomplete_and_checkpointed(self):
        self.run_case('''
a.last_turn_complete = True
checkpoints = []
a._checkpoint = lambda: checkpoints.append(json.loads(json.dumps(a.messages)))
for empty in (None, '', '   '):
    a.messages = [{'role':'system','content':'test'}, {'role':'tool','tool_call_id':'done','content':'VERIFIED_RESULT'}]
    before = json.loads(json.dumps(a.messages))
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response({'choices':[{'message':{'role':'assistant','content':empty,'reasoning_content':'PRIVATE_REASONING'},'finish_reason':'stop'}]})
    agent.requests.post = request
    answer = a._run_turn()
    assert len(calls) == 3, len(calls)
    assert a.last_turn_complete is False
    assert 'Task not complete' in answer, answer
    assert 'PRIVATE_REASONING' not in str(a.messages)
    assert a.messages[:-1] == before
    assert checkpoints[-1] == a.messages
''')

    def test_empty_summary_is_not_marked_complete(self):
        self.run_case('''
a.last_turn_complete = True
a._checkpoint = lambda: None
calls = []
def request(*args, **kwargs):
    calls.append(kwargs['json'])
    return Response({'choices':[{'message':{'role':'assistant','content':None},'finish_reason':'stop'}]})
agent.requests.post = request
answer = a._force_final_answer(None)
assert a.last_turn_complete is False, answer
assert 'Task not complete' in answer, answer
assert len(calls) == 3, len(calls)
assert all('tools' not in p for p in calls)
assert a.messages[-1]['content'] == answer
''')

    def test_refusal_is_visible_without_retry(self):
        self.run_case('''
a.last_turn_complete = True
a._checkpoint = lambda: None
calls = []
def request(*args, **kwargs):
    calls.append(True)
    return Response({'choices':[{'message':{'role':'assistant','content':None,'refusal':'Provider declined this request.'},'finish_reason':'stop'}]})
agent.requests.post = request
answer = a._run_turn()
assert answer == 'Provider declined this request.', answer
assert len(calls) == 1, calls
assert a.last_turn_complete is False
''')

    def test_empty_stop_does_not_promote_truncated_draft_to_complete(self):
        self.run_case('''
a.last_turn_complete = True
a._checkpoint = lambda: None
calls = []
def request(*args, **kwargs):
    calls.append(kwargs['json'])
    text, reason = ('unfinished draft', 'length') if len(calls) == 1 else (None, 'stop')
    return Response({'choices':[{'message':{'role':'assistant','content':text},'finish_reason':reason}]})
agent.requests.post = request
answer = a._run_turn()
assert a.last_turn_complete is False, answer
assert 'Task not complete' in answer, answer
assert len(calls) == 4, calls
assert all('unfinished draft' in str(p['messages']) for p in calls[1:]), calls
''')

    def test_stop_during_empty_recovery_prevents_another_request(self):
        self.run_case('''
calls = []
def request(*args, **kwargs):
    calls.append(True)
    return Response({'choices':[{'message':{'role':'assistant','content':''},'finish_reason':'stop'}]})
agent.requests.post = request
answer = a._run_turn(should_stop=lambda: bool(a._turn_completion_context))
assert answer == agent.CANCELLED_REPLY, answer
assert len(calls) == 1, calls
''')

    def test_summary_recovers_empty_without_enabling_tools(self):
        self.run_case('''
a.last_turn_complete = True
a._checkpoint = lambda: None
calls = []
def request(*args, **kwargs):
    calls.append(kwargs['json'])
    return Response({'choices':[{'message':{'role':'assistant','content':'' if len(calls)==1 else 'Verified summary'},'finish_reason':'stop'}]})
agent.requests.post = request
answer = a._force_final_answer(None)
assert answer == 'Verified summary', answer
assert len(calls) == 2
assert a.last_turn_complete is True
assert all('tools' not in p for p in calls)
''')

    def test_summary_transport_failure_is_incomplete(self):
        self.run_case('''
a.last_turn_complete = True
a._call_llm = lambda **kw: (_ for _ in ()).throw(agent.ZelineError('offline transport failure'))
answer = a._force_final_answer(None)
assert a.last_turn_complete is False, answer
assert 'Task not complete' in answer, answer
''')

    def test_content_filter_is_not_retried_or_marked_complete(self):
        self.run_case('''
a._checkpoint = lambda: None
for method in (lambda: a._run_turn(), lambda: a._force_final_answer(None)):
    a.last_turn_complete = True
    calls = []
    def request(*args, **kwargs):
        calls.append(True)
        return Response({'choices':[{'message':{'role':'assistant','content':None},'finish_reason':'content_filter'}]})
    agent.requests.post = request
    answer = method()
    assert a.last_turn_complete is False, answer
    assert 'filtered' in answer, answer
    assert len(calls) == 1, calls
''')

    def test_nonstream_finish_metadata(self):
        self.run_case('''
post({'choices':[{'message':{'role':'assistant','content':'partial','reasoning_content':'SECRET'},'finish_reason':'length'}]})
m = a._call_llm()
assert m.get('finish_reason') == 'length', m
assert m.get('reasoning_content') == 'SECRET', m
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


class ProviderQuotaContracts(unittest.TestCase):
    def test_quota_retry_guard_needs_no_local_health_observer(self):
        CompletionTests.run_case(self, r"""
for streamed in (False, True):
    calls = []
    a._streaming_enabled = lambda: streamed
    response = Response({'error': {'code': 'insufficient_quota'}})
    response.status_code = 429; response.ok = False
    response._content = response.text.encode()
    def request(*args, **kwargs):
        calls.append(True)
        return response
    agent.requests.post = request
    try:
        a._call_llm()
    except agent.ZelineError as error:
        assert str(error) == agent.provider_status_message(402, a.model)
    else:
        raise AssertionError('Quota exhaustion was not reported')
    assert len(calls) == 1, calls
""")
