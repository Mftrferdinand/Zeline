"""Tool progress counts actual calls without dropping the early activity row."""
import importlib
import unittest
from unittest import mock
import test_agent as fixtures


class ToolProgressCountsTests(unittest.TestCase):
    setUp=fixtures.AgentLoopTests.setUp
    tearDown=fixtures.AgentLoopTests.tearDown

    def live(self,**kwargs):
        t=importlib.import_module('zeline.gateways.telegram')
        return t,t._LiveStatus('https://api.fixture',11,**kwargs)

    def add(self,t,live,name,args):
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}):
            live.add(t._safe_progress_line(t._tool_progress_text(name,args)),invocation_key=t._tool_progress_key(name,args))

    def test_distinct_read_calls_have_distinct_uncounted_bubbles(self):
        t,live=self.live()
        for path in ['first.py','second.py','third.py']:self.add(t,live,'read_file',{'path':path})
        self.assertEqual(live._feed_text(),'📖 Reading third.py')
        self.assertEqual(len(live.lines),1)

    def test_repeated_identical_reads_count_calls_not_heartbeat_ticks(self):
        t,live=self.live()
        for _ in range(35):self.add(t,live,'read_file',{'path':'same.py'})
        with mock.patch.object(t,'_api_call',return_value={'ok':True}):
            for _ in range(3):live.tick()
        self.assertIn('(x35)',live._feed_text())
        self.assertEqual(len(live.lines),1)

    def test_different_shell_commands_never_inherit_the_read_or_shell_count(self):
        t,live=self.live(max_lines=2)
        self.add(t,live,'read_file',{'path':'first.py'})
        for i in range(30):self.add(t,live,'run_shell',{'command':f'printf fixture-{i}'})
        self.assertNotIn('first.py',live._feed_text())
        self.assertNotIn('(x',live._feed_text())
        self.assertEqual(live._feed_text(),'💻 Executing command…')

    def test_distinct_tools_use_new_bubbles_instead_of_overflow_groups(self):
        t,live=self.live(max_lines=3)
        for name,args in [('read_file',{'path':'first.py'}),('write_file',{'path':'second.py'}),('run_shell',{'command':'pwd'}),('execute_code',{'code':'pass'}),('system_env',{}),('load_skill',{'name':'latest-skill'})]:self.add(t,live,name,args)
        self.assertEqual(live._feed_text(),'📚 Loading skill: latest-skill')
        self.assertEqual(len(live.lines),1)

    def test_shell_repeat_badge_stays_inline_outside_code(self):
        t,live=self.live()
        for _ in range(2):self.add(t,live,'run_shell',{'command':'printf fixture'})
        self.assertEqual(live._feed_text(),'💻 Executing command… (x2)')

    def test_detach_keeps_old_counts_and_restarts_new_bubble_at_one(self):
        t,live=self.live()
        for _ in range(3):self.add(t,live,'read_file',{'path':'first.py'})
        with mock.patch.object(t,'_api_call',return_value={'ok':True}) as api:
            live.detach();old=api.call_args.kwargs['text'];self.assertIn('(x3)',old)
        self.add(t,live,'read_file',{'path':'new.py'});self.assertNotIn('(x',live._feed_text());self.assertIn('new.py',live._feed_text())

    def test_foreground_terminal_reports_actual_tool_or_provider_wait(self):
        cli=importlib.import_module('zeline.cli')
        read={'enabled':True,'agents':[{'active':True,'state':'Reading file','tool':'read_file'}]}
        detail=cli._gateway_activity_detail(['telegram'],read,enabled=True)
        self.assertIn('Reading file',detail);self.assertIn('read_file',detail)
        waiting={'enabled':True,'agents':[{'active':True,'state':'Waiting for Provider Response','tool':'read_file'}]}
        self.assertIn('Waiting for Provider Response',cli._gateway_activity_detail(['telegram'],waiting,enabled=True))
        self.assertIn('last tool',cli._gateway_activity_detail(['telegram'],waiting,enabled=True))

    def test_actual_trace_snapshot_drives_foreground_phase(self):
        cli=importlib.import_module('zeline.cli')
        runtime=importlib.import_module('zeline.runtime_state')
        state=runtime.RuntimeState();state.enable()
        with mock.patch.object(runtime,'STATE',state), runtime.Trace('telegram:fixture','fixture task') as trace:
            trace.tool(None)('read_file',{'path':'fixture.py'})
            self.assertNotIn('enabled',state.snapshot())
            detail=cli._gateway_activity_detail(['telegram'],state.snapshot(),enabled=state.enabled)
            self.assertIn('Recalling',detail);self.assertIn('read_file',detail)
            trace.iteration(None)(2,0)
            self.assertIn('Waiting for Provider Response',cli._gateway_activity_detail(['telegram'],state.snapshot(),enabled=state.enabled))
        self.assertIn('idle',cli._gateway_activity_detail(['telegram'],state.snapshot(),enabled=state.enabled))

    def test_foreground_distinguishes_observed_idle_from_unavailable_telemetry(self):
        cli=importlib.import_module('zeline.cli')
        self.assertIn('idle',cli._gateway_activity_detail(['telegram'],{'agents':[]},enabled=True))
        self.assertNotIn('idle',cli._gateway_activity_detail(['telegram'],{'agents':[]},enabled=False))
