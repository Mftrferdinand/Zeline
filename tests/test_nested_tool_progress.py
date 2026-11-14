"""Agent forwards real execute_code inner activity to the original UI/trace hook."""
import importlib
import json
import unittest
from pathlib import Path
from unittest import mock
import test_agent as fixtures

class NestedToolProgressTests(unittest.TestCase):
    setUp=fixtures.AgentLoopTests.setUp
    tearDown=fixtures.AgentLoopTests.tearDown

    def test_real_sdk_snippet_updates_file_and_emits_inner_file_and_shell_activity(self):
        workspace=Path(self.temp.name)/'workspace'
        workspace.mkdir()
        target=workspace/'fixture.txt'
        target.write_text('alpha\nbeta\ngamma\n')
        agent=self.agent_module.Zeline(identity='cli:local',tool_profile='full',workspace=workspace)
        code='from zeline_tools import read_file, patch, terminal\nr=read_file("fixture.txt",offset=2,limit=2)\nassert r["success"] and "beta" in r["output"]\np=patch("fixture.txt","beta","updated-beta")\nassert p["success"], p\ns=terminal("printf SDK_EXECUTED")\nassert s["success"] and "SDK_EXECUTED" in s["output"]\nprint("SDK_END_TO_END_OK")'
        calls={'role':'assistant','content':None,'tool_calls':[{'id':'sdk-call','type':'function','function':{'name':'execute_code','arguments':json.dumps({'code':code})}}]}
        t=importlib.import_module('zeline.gateways.telegram')
        live=t._LiveStatus('https://api.fixture',11)
        activity=[]
        def progress(name,args):
            activity.append((name,args))
            live.add(t._safe_progress_line(t._tool_progress_text(name,args)),invocation_key=t._tool_progress_key(name,args))
        with mock.patch.object(t,'_api_call',return_value={'ok':True,'result':{'message_id':7}}) as api, mock.patch.object(agent,'_call_llm',side_effect=[calls,{'role':'assistant','content':'Complete.'}]):
            self.assertEqual(agent.send('Exercise only the local synthetic fixture.',on_tool=progress),'Complete.')
        self.assertEqual(target.read_text(),'alpha\nupdated-beta\ngamma\n')
        self.assertEqual([name for name,args in activity],['execute_code','read_file','patch_file','run_shell'])
        self.assertIn('SDK_END_TO_END_OK',next(msg['content'] for msg in agent.messages if msg['role']=='tool'))
        sends=[c.kwargs['text'] for c in api.call_args_list if c.args[1]=='sendMessage']
        self.assertEqual(len(sends),1)
        feed=live._feed_text()
        self.assertIn('📖 Reading fixture.txt L2-3',feed)
        self.assertIn('🐍 Executing code: <code>from zeline_tools import read_file, patch, terminal</code>…',sends[0])
        self.assertIn('📝 Editing file: <code>fixture.txt</code>',feed)
        self.assertIn('printf SDK_EXECUTED',feed)
        self.assertEqual(len(live.lines),4)
        self.assertEqual(feed,'\n'.join(t._tool_progress_text(name,args) for name,args in activity))
        edits=[c for c in api.call_args_list if c.args[1]=='editMessageText']
        self.assertEqual(edits[-1].kwargs['text'],feed)
        self.assertTrue(all(c.kwargs['message_id']==7 and c.kwargs['parse_mode']=='HTML' for c in edits))
        self.assertIsNone(agent.executor._control('_nested_tool_callback'))

    def test_execute_code_forwards_original_tool_callback_in_invocation_context(self):
        agent=self.agent_module.Zeline(identity='cli:local',tool_profile='full',workspace=Path(self.temp.name)/'workspace')
        activity=[]
        code='from zeline_tools import read_file\nprint(read_file("fixture.py",offset=2,limit=2))'
        calls={'role':'assistant','content':None,'tool_calls':[{'id':'code-call','type':'function','function':{'name':'execute_code','arguments':json.dumps({'code':code})}}]}
        observed=[]
        def snippet(**kwargs):
            callback=agent.executor._control('_nested_tool_callback')
            observed.append(callback)
            if callback:callback('read_file',{'path':'fixture.py','offset':2,'limit':2})
            return 'exit=0\nfixture-result'
        with mock.patch.dict(agent.executor._handlers,{'execute_code':snippet}), mock.patch.object(agent,'_call_llm',side_effect=[calls,{'role':'assistant','content':'Complete.'}]):
            result=agent.send('Read the synthetic fixture.',on_tool=lambda name,args:activity.append((name,args)))
        self.assertEqual(result,'Complete.')
        self.assertIsNotNone(observed[0])
        self.assertEqual([name for name,args in activity],['execute_code','read_file'])
        self.assertEqual(activity[1][1],{'path':'fixture.py','offset':2,'limit':2})
        self.assertIsNone(agent.executor._control('_nested_tool_callback'))
