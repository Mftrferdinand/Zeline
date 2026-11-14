"""Main-session telemetry uses real engine/tool callbacks, never invented activity."""
import ast
import importlib
import inspect
import json
from pathlib import Path
import unittest
from unittest import mock
import urllib.parse
import urllib.request
import test_agent as fixtures

class MainTelemetryTests(unittest.TestCase):
    setUp=fixtures.AgentLoopTests.setUp
    tearDown=fixtures.AgentLoopTests.tearDown

    def setup_store(self,enabled=True):
        state=importlib.import_module('zeline.runtime_state')
        if enabled:state.STATE.enable()
        sessions=importlib.import_module('zeline.sessions')
        store=sessions.SessionStore(persistence=None)
        session=store.get_or_create('telegram:fixture','workspace',workspace=self.temp.name)
        return state,store,session

    def test_real_read_tool_is_visible_during_send_and_completed_afterwards(self):
        state,store,session=self.setup_store();path=Path(self.temp.name)/'telemetry.txt';path.write_text('native read marker')
        first={'role':'assistant','content':None,'tool_calls':[{'id':'fixture_read','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':str(path)})}}]}
        captured=[];calls=[]
        def provider(**_kwargs):
            calls.append(1)
            if len(calls)==1:return first
            snap=state.gateway_snapshot(sessions=store);captured.append(snap)
            self.assertEqual(snap['metrics']['active_agents'],1)
            self.assertEqual(snap['agents'][0]['tool'],'read_file')
            self.assertEqual(snap['agents'][0]['model'],'test-model')
            return {'role':'assistant','content':'Finished reading.'}
        with mock.patch.object(session.agent,'_call_llm',side_effect=provider):
            reply=store.send('telegram:fixture','Read the local fixture.','workspace',on_tool_result=lambda n,a,r:captured.append(r))
        self.assertEqual(reply,'Finished reading.')
        self.assertTrue(any(isinstance(x,str) and 'native read marker' in x for x in captured))
        snap=state.gateway_snapshot(sessions=store)
        self.assertEqual(snap['metrics']['active_agents'],0)
        self.assertEqual(snap['agents'][0]['status'],'completed')
        labels=[e['label'] for e in snap['events']]
        self.assertIn('Tool completed',labels);self.assertIn('Task completed',labels)

    def test_main_telemetry_reaches_actual_authenticated_http_api(self):
        state,store,session=self.setup_store();module=importlib.import_module('zeline.runtime_dashboard')
        dashboard=module.RuntimeDashboard(lambda:state.gateway_snapshot(sessions=store),port=0)
        dashboard.start();self.addCleanup(dashboard.stop)
        observed=[]
        def provider(**_kwargs):
            token=urllib.parse.parse_qs(urllib.parse.urlsplit(dashboard.owner_url()).fragment)['token'][0]
            req=urllib.request.Request(urllib.parse.urljoin(dashboard.url,'api/runtime'),headers={'Authorization':'Bearer '+token})
            with urllib.request.urlopen(req,timeout=3) as response:observed.append(json.loads(response.read()))
            return {'role':'assistant','content':'Done.'}
        with mock.patch.object(session.agent,'_call_llm',side_effect=provider):
            self.assertEqual(store.send('telegram:fixture','Check telemetry.','workspace'),'Done.')
        self.assertEqual(observed[0]['metrics']['active_agents'],1)
        self.assertEqual(observed[0]['agents'][0]['gateway'],'telegram')
        self.assertEqual(observed[0]['agents'][0]['model'],'test-model')

    def test_cancellation_is_not_counted_as_success(self):
        state,store,session=self.setup_store();ticket=store.admit('telegram:fixture','workspace')
        def provider(**_kwargs):
            ticket.cancel('stop');return {'role':'assistant','content':'Done.'}
        try:
            with mock.patch.object(session.agent,'_call_llm',side_effect=provider):reply=store.send('telegram:fixture','Fixture.','workspace',ticket=ticket)
            self.assertEqual(reply,'Stopped.');self.assertEqual(state.STATE.snapshot()['agents'][0]['status'],'cancelled')
        finally:store.finish_turn(ticket)

    def test_exception_finishes_observer_without_leaking_exception_text(self):
        state,store,session=self.setup_store()
        with mock.patch.object(session.agent,'send',side_effect=ValueError('private exception detail')):
            with self.assertRaises(ValueError):store.send('telegram:fixture','Fixture.','workspace')
        snap=state.STATE.snapshot();self.assertEqual(snap['agents'][0]['status'],'error')
        self.assertNotIn('private exception detail',json.dumps(snap))

    def test_disabled_observer_preserves_plain_send(self):
        state,store,session=self.setup_store(enabled=False)
        with mock.patch.object(session.agent,'_call_llm',return_value={'role':'assistant','content':'Done.'}):
            self.assertEqual(store.send('telegram:fixture','Fixture.','workspace'),'Done.')
        self.assertEqual(state.STATE.snapshot()['agents'],[])

    def test_trace_preserves_none_stream_and_restores_provider_callback(self):
        state,store,session=self.setup_store();callback=mock.Mock();session.agent._on_provider=callback
        def send(*args,**kwargs):
            self.assertIsNone(kwargs['on_stream_delta'])
            session.agent._on_provider({'status':'healthy'})
            return 'Done.'
        with mock.patch.object(session.agent,'send',side_effect=send):store.send('telegram:fixture','Fixture.','workspace')
        self.assertIs(session.agent._on_provider,callback)
        self.assertEqual(state.STATE.snapshot()['provider_health']['status'],'healthy')

    def test_observer_is_enabled_before_adapter_threads_accept_messages(self):
        cli=importlib.import_module('zeline.cli');module=ast.parse(inspect.getsource(cli))
        tree=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='_run_gateway_loop')
        start=min(n.lineno for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run_all')
        enables=[n.lineno for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='enable']
        self.assertTrue(any(line<start for line in enables),'Queued Telegram messages can arrive before telemetry is enabled')
