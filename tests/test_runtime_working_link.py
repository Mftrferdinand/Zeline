"""Owner-private localhost runtime link in the existing active Working line."""
import html
import importlib
from types import SimpleNamespace
import unittest
from unittest import mock
import test_agent as fixtures

URL='http://127.0.0.1:8765/#token=fixture&view=live'

class RuntimeWorkingLinkTests(unittest.TestCase):
    setUp=fixtures.AgentLoopTests.setUp
    tearDown=fixtures.AgentLoopTests.tearDown

    def test_runtime_stays_registered_alongside_working_hyperlink(self):
        commands=self.gateway()._telegram_commands()
        self.assertEqual(sum(row['command']=='runtime' for row in commands),1)
        self.assertTrue(any(row['command']=='status' for row in commands))

    def gateway(self):
        return importlib.import_module('zeline.gateways.telegram')

    def live(self):
        t=self.gateway()
        live=t._LiveStatus('https://api.fixture',11,runtime_url=URL)
        live.turn_started-=40
        live.lines=['📖 Reading file: fixture.txt']
        live.phase='tool'
        return t,live

    def test_working_has_exactly_one_clickable_runtime_ui_link(self):
        t,live=self.live()
        text=live._render()
        self.assertIn('Working',text)
        self.assertIn('/stop',text)
        self.assertNotIn('Runtime UI',text)
        self.assertEqual(text.count('>Working</a>'),1)
        self.assertIn(f'<a href="{html.escape(URL,quote=True)}">Working</a>',text)

    def test_heartbeat_edits_same_bubble_in_html_without_link_preview(self):
        t,live=self.live();live.message_id=44
        with mock.patch.object(t,'_api_call',return_value={'ok':True}) as api:
            live._push_locked(force=True)
            live._push_locked(force=True)
        self.assertEqual(api.call_count,2)
        for call in api.call_args_list:
            self.assertEqual(call.args[1],'editMessageText')
            self.assertEqual(call.kwargs['message_id'],44)
            self.assertEqual(call.kwargs['parse_mode'],'HTML')
            self.assertTrue(call.kwargs['disable_web_page_preview'])
            self.assertEqual(call.kwargs['text'].count('>Working</a>'),1)

    def test_link_removed_with_active_header_on_finalize(self):
        t,live=self.live();live.message_id=44
        with mock.patch.object(t,'_api_call',return_value={'ok':True}) as api:
            live.finalize()
        self.assertNotIn('<a href=',api.call_args.kwargs['text'])

    def test_thinking_and_waiting_for_user_are_unchanged(self):
        t,live=self.live();live.lines=[];live.turn_started-=100
        self.assertNotIn('<a href=',live._header())
        live.phase='awaiting_user'
        self.assertNotIn('<a href=',live._header())

    def test_same_owner_url_as_runtime_command_only_in_owner_dm(self):
        t=self.gateway();dashboard=SimpleNamespace(owner_url=mock.Mock(return_value=URL));sessions=SimpleNamespace(_runtime_dashboard=dashboard)
        with mock.patch.object(t.config,'GATEWAYS',{'telegram':{'allowed':[11]}}):
            self.assertEqual(t._owner_runtime_url(sessions,11),URL)
            self.assertEqual(t._owner_runtime_url(sessions,22),'')
            self.assertEqual(t._owner_runtime_url(sessions,-11),'')
        dashboard.owner_url.assert_called_once()

    def test_read_rows_have_no_blank_gap_across_rounds_or_finalize(self):
        t,live=self.live()
        live.lines=['📖 Reading file: main.ts','📖 Reading file: competition.yaml L1–140']
        live._read_breaks={1}
        expected='\n'.join(live.lines)
        self.assertEqual(live._feed_text(),expected)
        self.assertEqual(live._feed_text(finalize=True),expected)
        self.assertTrue(live._render().startswith(expected+'\n\n'))

    def test_spacing_fix_preserves_blank_lines_inside_command_blocks(self):
        t,live=self.live()
        command='<pre><code class="language-shell">printf first\n\nprintf second</code></pre>'
        live.lines=['📖 Reading file: main.ts',command,'📖 Reading file: config.yaml']
        self.assertIn('printf first\n\nprintf second',live._feed_text())
        self.assertIn('printf first\n\nprintf second',live._feed_text(finalize=True))

    def test_missing_dashboard_is_not_an_error(self):
        t=self.gateway()
        with mock.patch.object(t.config,'GATEWAYS',{'telegram':{'allowed':[11]}}):
            self.assertEqual(t._owner_runtime_url(SimpleNamespace(),11),'')

    def test_agent_delivery_automatically_wires_runtime_link(self):
        t=self.gateway();sessions=SimpleNamespace(_runtime_dashboard=SimpleNamespace(owner_url=lambda:URL),send=lambda **kwargs:'Done.',reflect=lambda *args,**kwargs:None)
        with mock.patch.object(t.config,'GATEWAYS',{'telegram':{'allowed':[11]}}),mock.patch.object(t,'_LiveStatus',wraps=t._LiveStatus) as cls,mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':44}}):
            t._send_agent_reply('https://api.fixture',sessions,chat_id=11,identity='telegram:11',text='Fixture',tool_profile='workspace')
        self.assertEqual(cls.call_args.kwargs['runtime_url'],URL)

    def test_gateway_run_owns_dashboard_start_and_finally_cleanup(self):
        import ast
        import inspect
        from zeline import cli
        tree=ast.parse(inspect.getsource(cli))
        def calls(nodes,name):
            return [n for root in nodes for n in ast.walk(root) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='runtime_state' and n.func.attr==name]
        self.assertEqual(len(calls([tree],'start_dashboard')),1)
        self.assertTrue(any(calls(node.finalbody,'stop_dashboard') for node in ast.walk(tree) if isinstance(node,ast.Try)))
