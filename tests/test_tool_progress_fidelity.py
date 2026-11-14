"""File/code progress reveals real arguments without replacing earlier read targets."""
import importlib
import unittest
from unittest import mock
import test_agent as fixtures

class ToolDetailTests(unittest.TestCase):
    def test_whole_file_read_does_not_invent_a_500_line_window(self):
        t = importlib.import_module('zeline.gateways.telegram')
        self.assertEqual(t._tool_progress_text('read_file', {'path':'telegram.py','limit':0}), '📖 Reading telegram.py')
        self.assertEqual(t._tool_progress_text('read_file', {'path':'telegram.py','offset':4110,'limit':0}), '📖 Reading telegram.py L4110+')

    def test_invalid_range_does_not_crash_the_progress_callback(self):
        t = importlib.import_module('zeline.gateways.telegram')
        for value in ['oops', {}, float('inf')]:
            self.assertEqual(t._tool_progress_text('read_file', {'path':'agent.py','offset':value,'limit':60}), '📖 Reading agent.py')

    def test_code_label_is_compact_without_imports_actions_or_private_literals(self):
        t = importlib.import_module('zeline.gateways.telegram')
        for code in ['from zeline_tools import patch\npatch("x","a","b")', 'import zeline_tools as zt\nzt.read_file("x")', 'print("SYNTHETIC_PRIVATE_LITERAL")']:
            with self.subTest(code=code):
                self.assertEqual(t._tool_progress_text('execute_code', {'code':code}), '🐍 Executing code with zeline_tools...')

    def test_executing_repeats_update_one_row_with_the_requested_suffix(self):
        t = importlib.import_module('zeline.gateways.telegram')
        live=t._LiveStatus('https://api.fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}):
            for code,expected in [
                ('from zeline_tools import read_file\nread_file("x")','🐍 Executing code with zeline_tools...'),
                ('from zeline_tools import read_file\nread_file("x")','🐍 Executing code with zeline_tools...(2x)'),
                ('from zeline_tools import read_file\nread_file("x")','🐍 Executing code with zeline_tools...(3x)'),
            ]:
                live.add(t._tool_progress_text('execute_code', {'code':code}),invocation_key=t._tool_progress_key('execute_code',{'code':code}))
                live.tick()
                self.assertEqual(live._feed_text(),expected)
                self.assertEqual(len(live.lines),1)
            live.tick()
            self.assertEqual(live._feed_text(),'🐍 Executing code with zeline_tools...(3x)')

    def test_new_bubble_resets_the_executing_suffix(self):
        t = importlib.import_module('zeline.gateways.telegram')
        live=t._LiveStatus('https://api.fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}):
            for _ in range(3):live.add(t._tool_progress_text('execute_code',{'code':'pass'}))
            live.detach()
            live.add(t._tool_progress_text('execute_code',{'code':'pass'}))
        self.assertEqual(live._feed_text(),'🐍 Executing code with zeline_tools...')

    def test_distinct_read_targets_are_retained_in_separate_messages(self):
        t=importlib.import_module('zeline.gateways.telegram');live=t._LiveStatus('fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}) as api:
            for path,offset,limit in [('telegram.py',4110,72),('agent.py',855,60)]:
                args={'path':path,'offset':offset,'limit':limit}
                live.add(t._tool_progress_text('read_file',args),invocation_key=t._tool_progress_key('read_file',args))
        self.assertEqual(live._feed_text(),'📖 Reading agent.py L855-914')
        sends=[c.kwargs['text'] for c in api.call_args_list if c.args[1]=='sendMessage']
        self.assertEqual(len(sends),2)
        self.assertIn('📖 Reading telegram.py L4110-4181',sends[0])
        self.assertNotIn('(x2)',sends[1])

    def test_rereading_an_earlier_file_starts_a_fresh_uncounted_bubble(self):
        t=importlib.import_module('zeline.gateways.telegram');live=t._LiveStatus('fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}) as api:
            for path in ['first.py','second.py','first.py']:
                live.add(t._tool_progress_text('read_file',{'path':path,'offset':1,'limit':20}))
        self.assertEqual(live._feed_text(),'📖 Reading first.py L1-20')
        self.assertEqual(sum(c.args[1]=='sendMessage' for c in api.call_args_list),3)

    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def test_file_name_and_requested_line_window(self):
        t = importlib.import_module('zeline.gateways.telegram')
        self.assertEqual(t._tool_progress_text('read_file', {'path':'/workspace/gateways/telegram.py','offset':4110,'limit':72}), '📖 Reading telegram.py L4110-4181')
        self.assertEqual(t._tool_progress_text('read_file', {'path':'agent.py','offset':855,'limit':60}), '📖 Reading agent.py L855-914')
