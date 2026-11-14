"""One compact read invocation per bubble, exact repeats only."""
import unittest
from unittest import mock
from zeline.gateways import telegram


class ReadFileSpacingTests(unittest.TestCase):
    def setUp(self):
        patcher=mock.patch.object(telegram,'_api_call',return_value={'ok':True,'result':{'message_id':10}})
        self.api=patcher.start();self.addCleanup(patcher.stop)
        self.live=telegram._LiveStatus('fixture',1)

    def read(self,name):
        args={'path':name,'offset':1,'limit':40}
        self.live.add(telegram._tool_progress_text('read_file',args),invocation_key=telegram._tool_progress_key('read_file',args))

    def test_read_file_name_uses_normal_text_not_monospace(self):
        rendered=telegram._tool_progress_text('read_file',{'path':'somewhere/NOTES<draft>&.md','offset':1,'limit':60})
        self.assertEqual(rendered,'📖 Reading NOTES&lt;draft&gt;&amp;.md L1-60')
        self.assertNotIn('<code>',rendered)
        self.assertEqual(telegram._tool_progress_text('read_file',{'path':'somewhere/PIPELINE.md'}),'📖 Reading PIPELINE.md')

    def test_round_changes_do_not_count_different_files(self):
        self.live.set_iteration(1,20);self.read('NOTES.md');self.read('PIPELINE.md')
        self.live.set_iteration(2,20);self.read('TickLinkedListPopInvariants.t.sol')
        self.assertEqual(self.live._render(),'📖 Reading TickLinkedListPopInvariants.t.sol L1-40')
        self.assertEqual(sum(c.args[1]=='sendMessage' for c in self.api.call_args_list),3)
        self.assertNotIn('\n',self.live._render())

    def test_live_final_and_detach_share_identical_call_count_and_reset(self):
        for finish in ('finalize','detach'):
            with self.subTest(finish=finish):
                self.live=telegram._LiveStatus('fixture',1)
                self.live.set_iteration(1,20);self.read('NOTES.md')
                self.live.set_iteration(2,20);self.read('NOTES.md')
                expected='📖 Reading NOTES.md L1-40 (x2)'
                self.assertEqual(self.live._render(),expected)
                getattr(self.live,finish)()
                edits=[c.kwargs['text'] for c in self.api.call_args_list if c.args[1]=='editMessageText']
                self.assertEqual(edits[-1],expected)
                if finish=='detach':
                    self.read('ANOTHER.md');self.assertEqual(self.live._render(),'📖 Reading ANOTHER.md L1-40')

    def test_interleaved_writing_starts_new_uncounted_read_bubble(self):
        self.live.max_lines=2
        self.read('OLD.md');self.read('NOTES.md');self.read('PIPELINE.md')
        self.live.add('📝 Writing <code>OUT.md</code>')
        self.live.set_iteration(3,20);self.read('NEXT.md')
        self.assertEqual(self.live._render(),'📖 Reading NEXT.md L1-40')
        self.assertEqual(sum(c.args[1]=='sendMessage' for c in self.api.call_args_list),5)
        self.assertEqual(len(self.live.lines),1)
