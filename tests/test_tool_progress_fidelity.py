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

    def test_code_label_previews_the_real_first_line_with_html_escaping(self):
        t = importlib.import_module('zeline.gateways.telegram')
        for code,preview in [
            ('from zeline_tools import patch\npatch("x","a","b")','from zeline_tools import patch'),
            ('import zeline_tools as zt\nzt.read_file("x")','import zeline_tools as zt'),
            ('print("<fixture> & value")','print("&lt;fixture&gt; &amp; value")'),
        ]:
            with self.subTest(code=code):
                self.assertEqual(t._tool_progress_text('execute_code', {'code':code}), f'🐍 Executing code: <code>{preview}</code>…')

    def test_executing_repeats_update_one_row_with_the_requested_suffix(self):
        t = importlib.import_module('zeline.gateways.telegram')
        live=t._LiveStatus('https://api.fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}):
            for code,expected in [
                ('from zeline_tools import read_file\nread_file("x")','🐍 Executing code: <code>from zeline_tools import read_file</code>…'),
                ('from zeline_tools import read_file\nread_file("x")','🐍 Executing code: <code>from zeline_tools import read_file</code>… (x2)'),
                ('from zeline_tools import read_file\nread_file("x")','🐍 Executing code: <code>from zeline_tools import read_file</code>… (x3)'),
            ]:
                live.add(t._tool_progress_text('execute_code', {'code':code}),invocation_key=t._tool_progress_key('execute_code',{'code':code}))
                live.tick()
                self.assertEqual(live._feed_text(),expected)
                self.assertEqual(len(live.lines),1)
            live.tick()
            self.assertEqual(live._feed_text(),'🐍 Executing code: <code>from zeline_tools import read_file</code>… (x3)')

    def test_new_bubble_resets_the_executing_suffix(self):
        t = importlib.import_module('zeline.gateways.telegram')
        live=t._LiveStatus('https://api.fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}):
            for _ in range(3):live.add(t._tool_progress_text('execute_code',{'code':'pass'}))
            live.detach()
            live.add(t._tool_progress_text('execute_code',{'code':'pass'}))
        self.assertEqual(live._feed_text(),'🐍 Executing code: <code>pass</code>…')

    def test_distinct_read_targets_are_retained_in_one_edited_message(self):
        t=importlib.import_module('zeline.gateways.telegram');live=t._LiveStatus('fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}) as api:
            for path,offset,limit in [('telegram.py',4110,72),('agent.py',855,60)]:
                args={'path':path,'offset':offset,'limit':limit}
                live.add(t._tool_progress_text('read_file',args),invocation_key=t._tool_progress_key('read_file',args))
        self.assertEqual(live._feed_text(),'📖 Reading telegram.py L4110-4181\n📖 Reading agent.py L855-914')
        sends=[c.kwargs['text'] for c in api.call_args_list if c.args[1]=='sendMessage']
        self.assertEqual(len(sends),1)
        self.assertIn('📖 Reading telegram.py L4110-4181',sends[0])
        self.assertNotIn('(x2)',live._feed_text())
        self.assertEqual(api.call_args.kwargs['text'],live._feed_text())
        self.assertEqual(api.call_args.kwargs['message_id'],7)

    def test_rereading_an_earlier_file_retains_a_fresh_uncounted_row(self):
        t=importlib.import_module('zeline.gateways.telegram');live=t._LiveStatus('fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}) as api:
            for path in ['first.py','second.py','first.py']:
                args={'path':path,'offset':1,'limit':20}
                live.add(t._tool_progress_text('read_file',args),invocation_key=t._tool_progress_key('read_file',args))
        self.assertEqual(live._feed_text(),'📖 Reading first.py L1-20\n📖 Reading second.py L1-20\n📖 Reading first.py L1-20')
        self.assertEqual(sum(c.args[1]=='sendMessage' for c in api.call_args_list),1)
        self.assertEqual(len(live.lines),3)
        self.assertNotIn('(x',live._feed_text())

    def test_same_code_preview_with_different_body_retains_both_invocations(self):
        t=importlib.import_module('zeline.gateways.telegram');live=t._LiveStatus('fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}):
            for code in ['import zeline_tools\nprint("first")','import zeline_tools\nprint("second")']:
                args={'code':code}
                live.add(t._tool_progress_text('execute_code',args),invocation_key=t._tool_progress_key('execute_code',args))
        self.assertEqual(live.lines,['🐍 Executing code: <code>import zeline_tools</code>…']*2)
        self.assertEqual(live._feed_text(),'\n'.join(live.lines))
        self.assertNotIn('(x',live._feed_text())

    def test_identical_read_basename_different_paths_or_ranges_are_distinct(self):
        t=importlib.import_module('zeline.gateways.telegram');live=t._LiveStatus('fixture',11)
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}):
            for path,offset in [('one/fixture.py',1),('two/fixture.py',1),('two/fixture.py',21)]:
                args={'path':path,'offset':offset,'limit':20}
                live.add(t._tool_progress_text('read_file',args),invocation_key=t._tool_progress_key('read_file',args))
        self.assertEqual(live._feed_text(),'📖 Reading fixture.py L1-20\n📖 Reading fixture.py L1-20\n📖 Reading fixture.py L21-40')
        self.assertEqual(len(live.lines),3)
        self.assertNotIn('(x',live._feed_text())

    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def test_file_name_and_requested_line_window(self):
        t = importlib.import_module('zeline.gateways.telegram')
        self.assertEqual(t._tool_progress_text('read_file', {'path':'/workspace/gateways/telegram.py','offset':4110,'limit':72}), '📖 Reading telegram.py L4110-4181')
        self.assertEqual(t._tool_progress_text('read_file', {'path':'agent.py','offset':855,'limit':60}), '📖 Reading agent.py L855-914')
