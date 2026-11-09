"""Selective automatic retrieval through real send(), with offline provider control."""
import unittest
from unittest.mock import patch
from reliability_cases import execution_cases as fixture

class SelectiveRecallTests(unittest.TestCase):
    setUp = fixture.ExecutionReliabilityIntegration.setUp

    def agent(self):
        return self.agent_module.Zeline(identity='fixture:selective-recall', workspace=str(self.root), tool_profile='workspace')

    def test_greetings_and_acknowledgments_do_not_lookup_memory(self):
        agent=self.agent()
        with patch.object(agent,'_run_turn',return_value='Acknowledged.'), patch.object(agent.executor.memory,'recall',return_value={'context':'','records':[]}) as recall:
            for text in ['halo','makasih','oke','ya gas','lanjut']:
                agent.send(text)
        self.assertEqual(recall.call_count,0,'Automatic retrieval ran for lightweight chat without missing context')

    def test_related_followup_reuses_memory_context_without_lookup(self):
        agent=self.agent()
        contexts=[]
        def answer(**callbacks):
            contexts.append(agent._turn_memory_context)
            return 'Verified answer.'
        saved={'context':'[saved preference] Fixture context.', 'records':[]}
        with patch.object(agent,'_run_turn',side_effect=answer), patch.object(agent.executor.memory,'recall',return_value=saved) as recall:
            agent.send('Analisa pipeline deployment ProjectQuartz')
            agent.send('Jelaskan hasilnya')
        self.assertEqual(recall.call_count,1,'Related followup repeated the same retrieval')
        self.assertTrue(all('Fixture context.' in context for context in contexts),'Cached context was dropped instead of reused')

    def test_memory_file_change_invalidates_followup_cache(self):
        agent=self.agent()
        with patch.object(agent,'_run_turn',return_value='Verified.'), patch.object(agent.executor.memory,'recall',return_value={'context':'fixture','records':[]}) as recall:
            agent.send('Analisa pipeline ProjectQuartz')
            agent.executor.memory.path.parent.mkdir(parents=True,exist_ok=True)
            agent.executor.memory.path.write_text('[]')
            agent.send('Jelaskan hasilnya')
        self.assertEqual(recall.call_count,2,'A changed memory store reused stale retrieved context')

    def test_expired_retrieved_fact_is_not_reused(self):
        agent=self.agent()
        expires=self.agent_module.time.time()+60
        saved={'context':'Temporary fact', 'records':[{'id':'fixture-expiry','revision':1,'expires_at':expires,'text':'Temporary fact'}]}
        with patch.object(agent,'_run_turn',return_value='Verified.'), patch.object(agent.executor.memory,'record_retrieval'), patch.object(agent.executor.memory,'recall',return_value=saved) as recall:
            agent.send('Analisa pipeline ProjectQuartz')
            with patch.object(self.agent_module.time,'time',return_value=expires+1):
                agent.send('Jelaskan hasilnya')
        self.assertEqual(recall.call_count,2,'Expired memory remained in the followup cache')

    def test_topic_switch_and_explicit_history_force_fresh_lookup(self):
        agent=self.agent()
        with patch.object(agent,'_run_turn',return_value='Verified.'), patch.object(agent.executor.memory,'recall',return_value={'context':'fixture','records':[]}) as recall:
            for text in ['Analisa pipeline ProjectQuartz','Audit kontrak TokenViolet','Ingat preferensi saya sebelumnya']:
                agent.send(text)
        self.assertEqual(recall.call_count,3)

    def test_fact_revision_invalidates_cache_without_changing_legacy_file(self):
        agent=self.agent()
        store=agent.executor.memory
        with patch.object(agent,'_run_turn',return_value='Verified.'), patch.object(store,'recall',return_value={'context':'fixture','records':[]}) as recall:
            agent.send('Analisa pipeline ProjectQuartz')
            with store._transaction() as (conn, records):
                conn.execute('INSERT INTO memory_revisions(owner,id,revision,payload) VALUES (?,?,?,?)',(store.key,'new-fixture-fact',1,'{}'))
            agent.send('Jelaskan hasilnya')
        self.assertEqual(recall.call_count,2)

    def test_cache_age_is_bounded(self):
        agent=self.agent()
        with patch.object(agent,'_run_turn',return_value='Verified.'), patch.object(agent.executor.memory,'recall',return_value={'context':'fixture','records':[]}) as recall:
            agent.send('Analisa pipeline ProjectQuartz')
            agent._automatic_recall_cache['at']-=31
            agent.send('Jelaskan hasilnya')
        self.assertEqual(recall.call_count,2)

    def test_concurrent_revision_during_recall_does_not_get_new_cache_signature(self):
        agent=self.agent()
        store=agent.executor.memory
        calls=[]
        contexts=[]
        def recall(text):
            calls.append(text)
            if len(calls)==1:
                with store._transaction() as (conn, records):
                    conn.execute('INSERT INTO memory_revisions(owner,id,revision,payload) VALUES (?,?,?,?)',(store.key,'concurrent-fixture',1,'{}'))
                return {'context':'OLD revision','records':[]}
            return {'context':'NEW revision','records':[]}
        def answer(**kwargs):
            contexts.append(agent._turn_memory_context)
            return 'Verified.'
        with patch.object(agent,'_run_turn',side_effect=answer), patch.object(store,'recall',side_effect=recall):
            agent.send('Analisa pipeline ProjectQuartz')
            agent.send('Jelaskan hasilnya')
        self.assertEqual(len(calls),2)
        self.assertIn('NEW revision',contexts[-1])

    def test_revision_during_reuse_never_relabels_old_context(self):
        agent=self.agent()
        store=agent.executor.memory
        saved={'context':'Fixture context','records':[]}
        def revise(*args):
            with store._transaction() as (conn, records):
                conn.execute('INSERT INTO memory_revisions(owner,id,revision,payload) VALUES (?,?,?,?)',(store.key,'reuse-fixture',1,'{}'))
        with patch.object(agent,'_run_turn',return_value='Verified.'), patch.object(store,'recall',return_value=saved) as recall:
            agent.send('Analisa pipeline ProjectQuartz')
            with patch.object(store,'record_retrieval',side_effect=revise):
                agent.send('Jelaskan hasilnya')
            agent.send('Jelaskan lagi')
        self.assertEqual(recall.call_count,2,'Concurrent update was attached to stale cached context')

    def test_deleted_fact_invalidates_cache_even_with_retained_revision_history(self):
        agent=self.agent()
        store=agent.executor.memory
        self.assertTrue(store.apply([{'action':'add','text':'Fixture deletion fact'}])['success'])
        with patch.object(agent,'_run_turn',return_value='Verified.'), patch.object(store,'recall',return_value={'context':'fixture','records':[]}) as recall:
            agent.send('Analisa pipeline ProjectQuartz')
            with store._transaction() as (conn, records):
                conn.execute('DELETE FROM memory_items WHERE owner=?',(store.key,))
            agent.send('Jelaskan hasilnya')
        self.assertEqual(recall.call_count,2,'Retained revision history hid deletion of an active fact')
