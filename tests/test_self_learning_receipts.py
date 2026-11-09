"""Disk-backed evidence contracts for reflection learning receipts."""


def test_duplicate_memory_is_not_a_new_learning_receipt(tmp_path, monkeypatch):
    from zeline import memory
    from zeline.agent import _format_reflection_actions
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('learning-proof')
    store.default_source = 'reflection'
    fact = 'Read the existing source before applying a targeted patch.'
    assert store.add(fact).startswith('OK, saved.')
    duplicate = store.add(fact)
    assert duplicate == 'That fact is already in memory.'
    assert _format_reflection_actions([('add_memory', {'fact': fact}, duplicate)]) is None


def test_saved_correction_is_recalled_by_new_agent_instance(tmp_path, monkeypatch):
    from zeline import memory
    from zeline.agent import Zeline
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    fact = 'Read the existing source before applying a targeted patch.'
    original = memory.MemoryStore('learning-proof-restart')
    original.default_source = 'reflection'
    assert original.add(fact).startswith('OK, saved.')
    restarted = Zeline(identity='learning-proof-restart', tool_profile='full')
    recalled = restarted.executor.memory.recall('existing source targeted patch')
    assert fact in recalled['context']
    other_owner = memory.MemoryStore('different-owner')
    assert fact not in other_owner.recall('existing source targeted patch')['context']
