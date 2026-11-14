"""A provider stall must recover without another user message or replaying tools."""
import json
import threading
import unittest
from pathlib import Path
from unittest import mock
import test_agent as fixtures


class AutonomousResumeTests(unittest.TestCase):
    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def test_stalled_provider_recovers_without_user_and_does_not_replay_write(self):
        agent = self.agent_module.Zeline(tool_profile='workspace', workspace=self.temp.name)
        gate = threading.Event()
        requests_seen, writes, replies, errors = [], [], [], []
        original_run = agent.executor.run

        def provider(*args, **kwargs):
            requests_seen.append(1)
            if len(requests_seen) == 1:
                return fixtures.FakeResponse({'choices':[{'message':{'role':'assistant','content':'','tool_calls':[
                    {'id':'write-once','type':'function','function':{'name':'write_file',
                     'arguments':json.dumps({'path':'evidence.txt','content':'persisted once'})}}]},'finish_reason':'tool_calls'}]})
            if len(requests_seen) == 2:
                gate.wait(5)
                content = 'LATE ABANDONED ANSWER'
            else:
                content = 'Recovered automatically; evidence preserved.'
            return fixtures.FakeResponse({'choices':[{'message':{'role':'assistant','content':content},'finish_reason':'stop'}]})

        def run(name,args):
            if name == 'write_file':
                writes.append(1)
            return original_run(name,args)

        def send():
            try:replies.append(agent.send('Write evidence then finish the task.'))
            except BaseException as e:errors.append(type(e).__name__)

        with mock.patch.object(self.agent_module.config,'PROVIDER_STALL_SECONDS',0.15,create=True), mock.patch.object(self.agent_module.requests,'post',side_effect=provider), mock.patch.object(agent.executor,'run',side_effect=run):
            worker = threading.Thread(target=send,daemon=True)
            worker.start()
            worker.join(2)
            completed_without_user = not worker.is_alive()
            gate.set()
            worker.join(3)
        self.assertTrue(completed_without_user,'Task stalled until an external wakeup; no autonomous recovery')
        self.assertEqual(errors,[])
        self.assertEqual(len(requests_seen),3)
        self.assertEqual(len(writes),1)
        self.assertEqual((Path(self.temp.name)/'evidence.txt').read_text(),'persisted once')
        self.assertEqual(replies,['Recovered automatically; evidence preserved.'])
        self.assertNotIn('LATE ABANDONED ANSWER',str(agent.messages))
