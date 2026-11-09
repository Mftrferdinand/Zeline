"""Recent conversation continuity must not be replaced by arbitrary cached recall."""
from unittest import mock
from tests import test_agent as support


class RecentConversationRecallTests(support.unittest.TestCase):
    setUp = support.AgentLoopTests.setUp
    tearDown = support.AgentLoopTests.tearDown

    def test_short_new_task_does_not_reuse_unrelated_recall(self):
        agent = self.agent_module.Zeline(identity='telegram:fixture', tool_profile='safe')
        contexts = []
        def answer(*args, **kwargs):
            contexts.append(agent._turn_memory_context)
            return {'content': 'fixture answer'}
        with mock.patch.object(agent.executor.memory, 'retrieval_version', return_value=('stable',)), \
                mock.patch.object(agent.executor.memory, 'recall', side_effect=lambda query: {
                    'context': 'recalled:' + query, 'records': []}) as recall, \
                mock.patch.object(agent, '_call_llm', side_effect=answer):
            agent.send('inspect amber architecture')
            agent.send('bikin invoice')
        self.assertEqual(recall.call_count, 2)
        self.assertIn('bikin invoice', contexts[-1])
        self.assertNotIn('amber', contexts[-1])

    def test_deictic_followup_keeps_live_task_without_another_recall(self):
        agent = self.agent_module.Zeline(identity='telegram:fixture', tool_profile='safe')
        with mock.patch.object(agent.executor.memory, 'retrieval_version', return_value=('stable',)), \
                mock.patch.object(agent.executor.memory, 'recall', return_value={
                    'context': 'fixture facts', 'records': []}) as recall, \
                mock.patch.object(agent, '_call_llm', return_value={'content': 'fixture answer'}):
            agent.send('inspect amber architecture')
            focused = agent.last_task_id
            agent.send('yang tadi')
        self.assertEqual(agent.last_task_id, focused)
        self.assertEqual(recall.call_count, 1)

    def test_topical_pin_survives_lexical_recall_assignment(self):
        agent = self.agent_module.Zeline(identity='telegram:fixture', tool_profile='safe',
                                        core_memory_in_prompt=True)
        contexts = []
        def answer(*args, **kwargs):
            contexts.append(agent._turn_memory_context)
            return {'content': 'fixture answer'}
        with mock.patch.object(agent.executor.memory, 'recall', return_value={
                    'context': 'LEXICAL_FACT', 'records': []}), \
                mock.patch.object(agent.executor.memory, 'prompt_block', return_value='TOPICAL_PIN'), \
                mock.patch.object(agent, '_call_llm', side_effect=answer):
            agent.send('inspect amber architecture')
        self.assertIn('LEXICAL_FACT', contexts[0])
        self.assertIn('TOPICAL_PIN', contexts[0])
