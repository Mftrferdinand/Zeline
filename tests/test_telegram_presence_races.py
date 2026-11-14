"""Deterministic interleavings of the actual presence worker, with no network."""
import ast
import inspect
import threading
import types
import unittest
import test_telegram_presence as fixtures


class PresenceRaceTests(unittest.TestCase):
    setUp = fixtures.TelegramPresenceTests.setUp
    tearDown = fixtures.TelegramPresenceTests.tearDown

    def run_interleaving(self, *, question_during_wait=False, question_before_request=False):
        t=self.telegram
        root=ast.parse(inspect.getsource(t._send_agent_reply))
        pulse=next(n for n in ast.walk(root) if isinstance(n,ast.FunctionDef) and n.name=='pulse')
        clock=[100.0];awaiting=threading.Event();sent=[];attempts=[]
        class Done:
            stopped=False
            def is_set(self):return self.stopped
            def wait(self,_delay):
                if question_during_wait:awaiting.set()
                return False
        done=Done()
        class Wake:
            calls=0
            def wait(self,_delay):
                self.calls+=1
                if self.calls>=3:done.stopped=True
                return True
            def clear(self):pass
        def api(_api,_method,**params):
            attempts.append(True)
            if question_before_request and len(attempts)==2:awaiting.set()
            if not params['cancelled']():sent.append(awaiting.is_set())
            clock[0]+=.1
            return {'ok':True}
        jobs=[]
        env={'_TYPING_INTERVAL_SECONDS':3.0,'_TYPING_TIMEOUT_SECONDS':2.0,
             'time':types.SimpleNamespace(monotonic=lambda:clock[0]),
             'done':done,'cancelled':lambda:False,'presence_wake':Wake(),
             'pending_question':[None],'awaiting_user':awaiting,
             'transport':types.SimpleNamespace(submit=lambda *a,**k:jobs.append((a,k))),
             'resume_question_status':lambda:None,'_api_call':api,'api':'offline','chat_id':1,
             'live':types.SimpleNamespace(tick=lambda:None)}
        exec(compile(ast.Module(body=[pulse],type_ignores=[]),'<actual-presence-worker>','exec'),env)
        env['pulse']()
        return sent,jobs

    def test_question_entering_during_coalescing_wait_suppresses_typing(self):
        sent,jobs=self.run_interleaving(question_during_wait=True)
        self.assertEqual(sent,[False],'Typing was dispatched after the human-wait transition')
        self.assertEqual(jobs,[],'Activity wakes recursively submitted progress ticks')

    def test_request_cancellation_rechecks_human_wait(self):
        sent,_=self.run_interleaving(question_before_request=True)
        self.assertEqual(sent,[False],'The request predicate ignored a newly pending human answer')
