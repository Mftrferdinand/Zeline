"""Unlimited productive work must not disable cancellation or stall recovery."""
import json
import unittest
from unittest import mock
import test_agent as fixtures


class UnboundedWorkTests(unittest.TestCase):
    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def test_zero_limits_continue_past_150_without_forced_summary(self):
        agent=self.agent_module.Zeline(tool_profile='workspace',workspace=self.temp.name)
        calls=[]
        def llm(**kwargs):
            if kwargs.get('use_tools') is False:
                return {'role':'assistant','content':'forced cutoff'}
            if len(calls)>=160:
                return {'role':'assistant','content':'Finished 160 productive steps.'}
            n=len(calls)
            return {'role':'assistant','content':'','tool_calls':[{'id':f'step-{n}','type':'function',
                'function':{'name':'read_file','arguments':json.dumps({'path':f'fixture-{n}.txt'})}}]}
        def run(name,args):
            calls.append(args['path'])
            return f'Verified progress {len(calls)}'
        with mock.patch.object(self.agent_module.config,'MAX_TOOL_ROUNDS',0),mock.patch.object(self.agent_module.config,'MAX_TURN_SECONDS',0),mock.patch.object(agent,'_call_llm',side_effect=llm),mock.patch.object(agent.executor,'run',side_effect=run),mock.patch.object(agent,'_checkpoint'):
            answer=agent.send('Complete all productive steps.')
        self.assertEqual(len(calls),160)
        self.assertEqual(answer,'Finished 160 productive steps.')
        self.assertTrue(agent.last_turn_complete)

    def test_zero_time_limit_survives_config_loading(self):
        cfg=self.agent_module.config._defaults()
        cfg['agent'].update(max_tool_rounds=0,max_turn_seconds=0)
        self.agent_module.config._set_runtime_values(cfg)
        self.assertEqual(self.agent_module.config.MAX_TOOL_ROUNDS,0)
        self.assertEqual(self.agent_module.config.MAX_TURN_SECONDS,0)

    def test_unlimited_work_does_not_shorten_user_question_timeout(self):
        from zeline import interaction
        with mock.patch.object(self.agent_module.config,'MAX_TURN_SECONDS',0),mock.patch.object(self.agent_module.config,'ASK_USER_TIMEOUT',180):
            self.assertEqual(interaction._timeout_seconds(),180)

    def test_setup_can_save_explicit_unlimited_limits(self):
        from zeline import cli
        cfg=self.agent_module.config._defaults()
        with mock.patch.object(cli.config,'stored_config_copy',return_value=cfg),mock.patch.object(cli,'_ask',side_effect=['Fixture','0','0','100','n','y']),mock.patch.object(cli.config,'save_config') as save,mock.patch('builtins.print'):
            result=cli.cmd_setup_agent()
        self.assertEqual(result,0)
        self.assertEqual(save.call_args[0][0]['agent']['max_turn_seconds'],0)

    def test_tool_errors_do_not_falsely_report_tool_budget_exhaustion(self):
        agent=self.agent_module.Zeline(tool_profile='workspace',workspace=self.temp.name)
        requested=[]
        def llm(**kwargs):
            if kwargs.get('use_tools') is False:
                raise self.agent_module.ZelineError('Provider unavailable')
            requested.append(1)
            return {'role':'assistant','content':'','tool_calls':[{'id':f'error-{len(requested)}','type':'function','function':{'name':'read_file','arguments':'{"path":"missing"}'}}]}
        with mock.patch.object(self.agent_module.config,'MAX_TOOL_ROUNDS',0),mock.patch.object(self.agent_module.config,'MAX_TURN_SECONDS',0),mock.patch.object(agent,'_call_llm',side_effect=llm),mock.patch.object(agent.executor,'run',return_value='ERROR: fixture failure'):
            answer=agent.send('Read fixture data.')
        self.assertNotIn('Batas tool tercapai',answer)
        self.assertIn('repeated tool failures',answer.lower())
        self.assertFalse(agent.last_turn_complete)

    def test_stop_still_works_with_unlimited_rounds_and_time(self):
        agent=self.agent_module.Zeline(tool_profile='workspace',workspace=self.temp.name)
        calls=[]
        def llm(**kwargs):
            if len(calls)>6:
                raise AssertionError('Cancellation was ignored')
            return {'role':'assistant','content':'','tool_calls':[{'id':f'stop-{len(calls)}','type':'function','function':{'name':'read_file','arguments':'{"path":"fixture"}'}}]}
        with mock.patch.object(self.agent_module.config,'MAX_TOOL_ROUNDS',0),mock.patch.object(self.agent_module.config,'MAX_TURN_SECONDS',0),mock.patch.object(agent,'_call_llm',side_effect=llm),mock.patch.object(agent.executor,'run',side_effect=lambda *args:calls.append(1) or 'verified'):
            answer=agent.send('Work until stopped.',should_stop=lambda:len(calls)>=3)
        self.assertEqual(len(calls),3)
        self.assertEqual(answer,self.agent_module.CANCELLED_REPLY)

    def test_parallel_tools_still_execute_with_unlimited_time(self):
        agent=self.agent_module.Zeline(tool_profile='workspace',workspace=self.temp.name)
        first={'role':'assistant','content':'','tool_calls':[{'id':f'parallel-{i}','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':str(i)})}} for i in range(2)]}
        final={'role':'assistant','content':'Parallel steps completed.'}
        with mock.patch.object(self.agent_module.config,'MAX_TOOL_ROUNDS',0),mock.patch.object(self.agent_module.config,'MAX_TURN_SECONDS',0),mock.patch.object(agent,'_call_llm',side_effect=[first,final]),mock.patch.object(agent.executor,'run',return_value='verified') as run:
            answer=agent.send('Read both fixtures.')
        self.assertEqual(run.call_count,2)
        self.assertEqual(answer,final['content'])

    def test_native_provider_recovery_remains_enabled_in_unlimited_mode(self):
        from test_provider_wait import ProviderWaitTests
        case=ProviderWaitTests('test_real_http_drip_recovers_without_a_new_user_message')
        case.setUp()
        try:
            with mock.patch.object(case.agent_module.config,'MAX_TOOL_ROUNDS',0),mock.patch.object(case.agent_module.config,'MAX_TURN_SECONDS',0):
                case.test_real_http_drip_recovers_without_a_new_user_message()
        finally:
            case.tearDown()
