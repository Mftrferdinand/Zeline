"""Foreground admission must cancel learning and execute a real file tool."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


class ForegroundPriorityIntegration(unittest.TestCase):
    def test_foreground_interrupts_reflection_and_runs_without_manual_continue(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"HOME":tmp,"ZELINE_HOME":tmp}):
            for name in list(sys.modules):
                if name == 'zeline' or name.startswith('zeline.'):
                    del sys.modules[name]
            config = importlib.import_module('zeline.config')
            config.PERSIST_SESSIONS = False
            module = importlib.import_module('zeline.sessions')
            store = module.SessionStore()
            identity = 'fixture:foreground-priority'
            session = store.get_or_create(identity, 'workspace', tmp)
            entered = threading.Event()
            cancelled = threading.Event()
            errors = []
            target = Path(tmp)/'foreground.txt'
            responses = iter([
                {'content':'','tool_calls':[{'id':'foreground-write','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':str(target),'content':'Foreground ran.\n'})}}]},
                {'content':'Foreground file written.','finish_reason':'stop'},
            ])
            def reflect(**kwargs):
                entered.set()
                until = time.monotonic()+2
                while not session.agent._cancelled():
                    if time.monotonic() > until:
                        errors.append('reflection ignored cancellation')
                        return None
                    cancelled.wait(0.005)
                cancelled.set()
                return 'obsolete learning result'
            def run_reflection():
                try:
                    store.reflect(identity)
                except Exception as exc:
                    errors.append(type(exc).__name__)
            with patch.object(session.agent,'reflect',side_effect=reflect), patch.object(session.agent,'_call_llm',side_effect=lambda **kwargs:next(responses)):
                thread = threading.Thread(target=run_reflection,daemon=True)
                thread.start()
                self.assertTrue(entered.wait(1), 'Reflection never entered the actual session lock')
                ticket = store.admit(identity, 'workspace', tmp, interrupt=True)
                try:
                    reply = store.send(identity, 'Write the foreground file now.', 'workspace', tmp, ticket=ticket)
                finally:
                    store.finish_turn(ticket)
                    thread.join(3)
            self.assertFalse(thread.is_alive(), 'Learning blocked the foreground turn')
            self.assertEqual(errors, [])
            self.assertTrue(cancelled.is_set())
            self.assertEqual(target.read_text(), 'Foreground ran.\n')
            self.assertIn('written', reply)
            self.assertFalse(session.running)


if __name__ == '__main__':
    unittest.main(verbosity=2)
