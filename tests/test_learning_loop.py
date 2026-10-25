"""Evidence-backed learning contracts; never use a live provider or live store."""
import pytest


@pytest.fixture
def memory_store(tmp_path, monkeypatch):
    from zeline import memory
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('loop-owner')
    store.default_source = 'reflection'
    return store


def test_atomic_memory_receipt_distinguishes_new_from_duplicate(memory_store):
    first = memory_store.add_receipt('Inspect the source before a targeted patch.')
    duplicate = memory_store.add_receipt('Inspect the source before a targeted patch.')
    assert first['version'] == 1
    assert first['kind'] == 'memory'
    assert first['state'] == 'created'
    assert first['record_id'] and first['revision'] == 1
    assert first['expires_at'] is not None
    assert duplicate['state'] == 'duplicate'
    assert duplicate['record_id'] == first['record_id']
    assert duplicate['revision'] == first['revision']
    assert len(memory_store.list()) == 1


def test_retrieval_provenance_survives_restart_without_claiming_use(memory_store):
    from zeline.memory import MemoryStore
    added = memory_store.add_receipt('Inspect the source before a targeted patch.')
    recalled = memory_store.recall('source targeted patch')
    memory_store.record_retrieval('turn-fixture', recalled['records'])
    memory_store.finish_learning_turn('turn-fixture', 'completed')
    fresh = MemoryStore('loop-owner')
    rows = fresh.retrievals('turn-fixture')
    assert len(rows) == 1
    assert rows[0]['record_id'] == added['record_id']
    assert rows[0]['revision'] == added['revision']
    assert rows[0]['stage'] == 'retrieved'
    assert rows[0]['turn_outcome'] == 'completed'
    assert rows[0]['assessment'] is None
    assert MemoryStore('another-owner').retrievals('turn-fixture') == []


def test_trusted_paired_assessment_updates_only_the_retrieved_revision_once(memory_store):
    added = memory_store.add_receipt('Inspect the source before a targeted patch.')
    memory_store.record_retrieval('paired-turn', memory_store.recall('source targeted patch')['records'])
    kwargs = dict(checker='fixture-contract-v1', case_id='source-first',
                  baseline=False, candidate=True, used=True)
    evidence = memory_store.assess_retrieval('paired-turn', added['record_id'], 1, **kwargs)
    assert evidence['outcome'] == 'gain'
    current = memory_store.records()[0]
    assert current['confidence'] > 0.6
    assert current['revision'] == 2
    again = memory_store.assess_retrieval('paired-turn', added['record_id'], 1, **kwargs)
    assert again['evidence_id'] == evidence['evidence_id']
    assert memory_store.records()[0]['revision'] == 2
    assert memory_store.retrievals('paired-turn')[0]['stage'] == 'assessed'


def test_formatter_renders_specific_receipts_without_claiming_duplicates():
    import json
    from zeline.agent import _format_reflection_actions
    receipt = dict(version=1, kind='memory', state='created', record_id='mem_fixture',
                   revision=1, summary='Inspect source before patching.', expires_at=123456)
    summary = _format_reflection_actions([('add_memory', {}, json.dumps(receipt))])
    assert summary is not None
    assert 'mem_fixture' in summary and 'Inspect source before patching.' in summary
    assert 'Self-Refinement' in summary
    assert _format_reflection_actions([('add_memory', {}, json.dumps({**receipt, 'state': 'duplicate'}))]) is None
    assert _format_reflection_actions([('resolve_lesson', {}, 'OK, lesson resolved.')]) is None


def test_reflection_dispatch_returns_atomic_memory_receipt(memory_store):
    import json
    from zeline.tools import ToolExecutor
    executor = ToolExecutor('loop-owner', profile='full')
    executor._reflection_mode = True
    fact = 'Inspect source before a targeted patch.'
    first = json.loads(executor.run('add_memory', {'fact': fact}))
    duplicate = json.loads(executor.run('add_memory', {'fact': fact}))
    assert first['state'] == 'created' and first['record_id']
    assert duplicate['state'] == 'duplicate'
    assert memory_store.list() == [fact]


def test_send_records_retrieval_without_claiming_task_success(memory_store, monkeypatch):
    from zeline.agent import Zeline
    memory_store.add_receipt('Inspect source before a targeted patch.')
    agent = Zeline(identity='loop-owner', tool_profile='full')
    monkeypatch.setattr(agent, '_run_turn', lambda **kwargs: 'A normal answer, not proof of task acceptance.')
    agent.send('source targeted patch')
    rows = memory_store.retrievals(agent.last_learning_turn_id)
    assert len(rows) == 1 and rows[0]['turn_outcome'] == 'completed'
    assert rows[0]['assessment'] is None and rows[0]['stage'] == 'retrieved'


def test_measured_regression_reduces_recall_priority(memory_store):
    good = memory_store.add_receipt('source guidance stable')
    bad = memory_store.add_receipt('source guidance risky')
    recalled = memory_store.recall('source guidance')
    assert recalled['records'][0]['id'] == bad['record_id']
    memory_store.record_retrieval('negative-turn', recalled['records'])
    memory_store.assess_retrieval('negative-turn', bad['record_id'], 1,
                                 checker='fixture-contract-v1', case_id='source-choice',
                                 baseline=True, candidate=False, used=True)
    assert memory_store.recall('source guidance')['records'][0]['id'] == good['record_id']


def test_memory_receipt_preserves_host_review_origin(memory_store):
    receipt = memory_store.add_receipt('Inspect source before a targeted patch.', origin_turn='review-fixture-1')
    assert receipt['origin_turn'] == 'review-fixture-1'
    assert memory_store.records()[0]['origin_turn'] == 'review-fixture-1'
    duplicate = memory_store.add_receipt('Inspect source before a targeted patch.', origin_turn='review-fixture-2')
    assert duplicate['origin_turn'] == 'review-fixture-1'


def test_feedback_links_original_revision_to_observed_task_results(memory_store):
    receipt = memory_store.add_receipt('Inspect source before patching.')
    memory_store.record_retrieval('feedback-turn', memory_store.recall('source')['records'])
    memory_store.assess_retrieval('feedback-turn', receipt['record_id'], 1,
                                 checker='fixture-contract-v1', case_id='source-choice',
                                 baseline=True, candidate=False, used=True)
    feedback = memory_store.learning_feedback('feedback-turn')
    assert feedback[0]['revision'] == 1
    assert feedback[0]['summary'] == 'Inspect source before patching.'
    assert feedback[0]['assessment']['outcome'] == 'regression'
    assert feedback[0]['assessment']['new_revision'] == 2


def test_reflection_consumes_measured_feedback_as_data(memory_store, monkeypatch):
    from zeline.agent import Zeline
    receipt = memory_store.add_receipt('Inspect source before patching.')
    memory_store.record_retrieval('review-feedback', memory_store.recall('source')['records'])
    memory_store.assess_retrieval('review-feedback', receipt['record_id'], 1,
                                 checker='fixture-contract-v1', case_id='source-choice',
                                 baseline=True, candidate=False, used=True)
    agent = Zeline(identity='loop-owner', tool_profile='full')
    agent.executor.memory = memory_store
    agent.last_learning_turn_id = 'review-feedback'
    prompts = []
    def call():
        prompts.append(agent.messages[-1]['content'])
        return {'content': 'NO_ACTION'}
    monkeypatch.setattr(agent, '_call_llm', call)
    assert agent.reflect(min_tool_calls=0) is None
    assert 'regression' in prompts[0] and receipt['record_id'] in prompts[0]


def test_executor_lesson_resolution_requires_real_success_reference(memory_store, tmp_path, monkeypatch):
    import json
    from zeline import lessons
    from zeline.tools import ToolExecutor
    store = lessons.LessonsStore(tmp_path / 'success-evidence.db')
    monkeypatch.setattr(lessons, '_store', store)
    store.record_failure('loop-owner', 'read_file', {'path': 'missing.txt'}, 'ERROR: missing')
    event = store.record_success('loop-owner', 'read_file', {'path': 'present.txt'})
    executor = ToolExecutor(identity='loop-owner', profile='full')
    result = executor.run('resolve_lesson', dict(tool='read_file', args_sig_contains='missing.txt',
                                               fix='Read the existing file instead.', evidence_id=event))
    receipt = json.loads(result)
    assert receipt['state'] == 'updated' and receipt['evidence_id'] == event
    schema = next(s for s in executor.schemas if s['function']['name'] == 'resolve_lesson')
    assert 'evidence_id' in schema['function']['parameters']['required']


def test_reflection_receives_only_owned_success_references(memory_store, tmp_path, monkeypatch):
    from zeline import lessons
    from zeline.agent import Zeline
    store = lessons.LessonsStore(tmp_path / 'recent-events.db')
    monkeypatch.setattr(lessons, '_store', store)
    event = store.record_success('loop-owner', 'read_file', {'path': 'present.txt'})
    foreign = store.record_success('someone-else', 'read_file', {'path': 'private.txt'})
    agent = Zeline(identity='loop-owner', tool_profile='full')
    prompts = []
    def call():
        prompts.append(agent.messages[-1]['content'])
        return {'content': 'NO_ACTION'}
    monkeypatch.setattr(agent, '_call_llm', call)
    agent.reflect(min_tool_calls=0)
    assert event in prompts[0] and foreign not in prompts[0]
    assert 'not task-quality proof' in prompts[0]


def test_reflection_includes_single_durable_project_correction(memory_store, monkeypatch):
    from zeline.agent import Zeline
    agent = Zeline(identity='loop-owner', tool_profile='full')
    prompts = []
    def call():
        prompts.append(agent.messages[-1]['content'])
        return {'content': 'NO_ACTION'}
    monkeypatch.setattr(agent, '_call_llm', call)
    agent.reflect(min_tool_calls=0)
    assert 'stable project convention' in prompts[0]
    assert 'does not require repeated failures' in prompts[0]
