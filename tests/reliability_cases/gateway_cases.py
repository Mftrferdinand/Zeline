"""Actual gateway -> session -> agent -> real file tool with blocked UI I/O."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


class GatewayToolNonblockingIntegration(unittest.TestCase):
    def test_blocked_narration_does_not_block_real_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'HOME':tmp,'ZELINE_HOME':tmp}):
            for name in list(sys.modules):
                if name == 'zeline' or name.startswith('zeline.'):
                    del sys.modules[name]
            config = importlib.import_module('zeline.config')
            config.PERSIST_SESSIONS = False
            sessions_module = importlib.import_module('zeline.sessions')
            gateway = importlib.import_module('zeline.gateways.telegram')
            store = sessions_module.SessionStore()
            identity = 'fixture:gateway-tool-acceptance'
            session = store.get_or_create(identity, 'workspace', tmp)
            target = Path(tmp)/'tool-ran.txt'
            entered = threading.Event()
            release = threading.Event()
            tool_finished = threading.Event()
            errors = []
            outcomes = []
            sent = []
            responses = iter([
                {'content':'Writing the requested file now.','tool_calls':[{'id':'only-write','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':str(target),'content':'Actual tool executed.\n'})}}]},
                {'content':'Verified: the requested file was written.','finish_reason':'stop'},
            ])
            real_run = session.agent.executor.run
            def run(name, args, *a, **kw):
                result = real_run(name, args, *a, **kw)
                tool_finished.set()
                return result
            def api(_api, method, **payload):
                if method == 'sendMessage':
                    if not entered.is_set():
                        entered.set()
                        if not release.wait(8):
                            raise AssertionError('Test transport was not released')
                    sent.append(str(payload.get('text','')))
                    return {'ok':True,'result':{'message_id':len(sent)}}
                return {'ok':True,'result':True}
            class Heartbeat:
                def join(self, **kwargs):
                    pass
            def send():
                try:
                    outcomes.append(gateway._send_agent_reply('fixture-api', store, chat_id=1, identity=identity,
                        text='Write the requested file now.', tool_profile='workspace'))
                except BaseException as exc:
                    errors.append(type(exc).__name__ + ': ' + str(exc))
            with patch.object(gateway,'_api_call',side_effect=api), patch.object(gateway,'_start_working_heartbeat',return_value=Heartbeat()), \
                 patch.object(session.agent,'_call_llm',side_effect=lambda **kwargs:next(responses)), \
                 patch.object(session.agent.executor,'run',side_effect=run):
                worker = threading.Thread(target=send,daemon=True)
                worker.start()
                try:
                    self.assertTrue(entered.wait(3), 'No actual narration transport call was observed')
                    progressed_while_transport_blocked = tool_finished.wait(1)
                finally:
                    release.set()
                    worker.join(5)
            self.assertFalse(worker.is_alive(), 'Gateway worker did not finalize')
            self.assertEqual(errors, [])
            self.assertTrue(progressed_while_transport_blocked, 'Actual file tool waited behind cosmetic/narration network I/O')
            self.assertEqual(target.read_text(), 'Actual tool executed.\n')
            self.assertEqual(outcomes, [True])
            self.assertEqual(sum('Verified:' in part for part in sent), 1, 'Final answer was lost or duplicated')


if __name__ == '__main__':
    unittest.main(verbosity=2)
