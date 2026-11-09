"""Internal reflection must not depend on discovery it is forbidden to call."""

def test_reflection_eagerly_exposes_only_allowed_learning_schemas(monkeypatch):
    from zeline import config, tool_index
    from zeline.tools import ToolExecutor
    monkeypatch.setattr(config, 'TOOL_SEARCH', True)
    monkeypatch.setattr(tool_index, 'MIN_CHARS_SAVED', 0)
    executor = ToolExecutor(identity='schema-fixture', profile='full')
    normal = {s['function']['name'] for s in executor.schemas}
    assert 'tool_search' in normal
    assert 'add_memory' not in normal
    executor._reflection_mode = True
    names = {s['function']['name'] for s in executor.schemas}
    assert names == {'manage_skill', 'load_skill', 'list_memory', 'add_memory', 'resolve_lesson'}
    with monkeypatch.context() as context:
        context.setattr(config, 'DISABLED_TOOLS', ('add_memory',))
        blocked = ToolExecutor(identity='schema-blocked', profile='full')
        blocked._reflection_mode = True
        assert 'add_memory' not in {s['function']['name'] for s in blocked.schemas}
        assert blocked.run('add_memory', {'fact': 'Fixture fact.'}).startswith('ERROR:')
    executor._reflection_mode = False
    assert {s['function']['name'] for s in executor.schemas} == normal
