"""Opt-in reported usage stays per-turn, nullable, and observer-isolated."""
import importlib
from unittest import mock
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_imports(tmp_path,monkeypatch):
    import sys
    monkeypatch.setenv("ZELINE_HOME",str(tmp_path/"isolated-home"))
    monkeypatch.setenv("HOME",str(tmp_path/"isolated-user"))
    saved={name:module for name,module in sys.modules.items() if name == "zeline" or name.startswith("zeline.")}
    for name in saved:
        sys.modules.pop(name,None)
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name,None)
        sys.modules.update(saved)


@pytest.mark.parametrize("payload,protocol,expected", [
    ({"usage":{"prompt_tokens":7,"completion_tokens":3}},"openai",(7,3,10)),
    ({"usage":{"input_tokens":7,"output_tokens":0}},"anthropic",(7,0,7)),
    ({"usage":{"total_tokens":12}},"openai",(None,None,12)),
    ({"usage":{"prompt_tokens":7}},"openai",(7,None,None)),
    ({"usage":{"prompt_tokens":True,"completion_tokens":-1}},"openai",(None,None,None)),
    ({},"openai",(None,None,None)),
])
def test_reported_usage_preserves_missing(payload,protocol,expected):
    usage = importlib.import_module("zeline.usage_stats")
    result = usage.reported_usage(payload, protocol)
    assert tuple(result[k] for k in ("input_tokens","output_tokens","total_tokens")) == expected


def test_observer_is_turn_scoped_and_independent_from_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZELINE_HOME", str(tmp_path/"home"))
    module = importlib.import_module("zeline.agent")
    agent = module.Zeline(identity="eval:usage", tool_profile="safe", workspace=str(tmp_path))
    agent.protocol = "openai"
    events=[]
    def reply(**kwargs):
        agent._record_usage({"usage":{"prompt_tokens":4,"completion_tokens":2}})
        return {"content":"fixture"}
    with mock.patch.object(agent,"_call_llm",side_effect=reply), mock.patch.object(agent,"_usage_store",side_effect=OSError("fixture DB unavailable")):
        assert agent.send("fixture",on_usage=events.append)=="fixture"
        assert len(events)==1
        agent.send("next fixture")
        assert len(events)==1
        assert agent._on_usage is None
        def broken(event):
            raise RuntimeError("observer failure")
        assert agent.send("observer failure fixture",on_usage=broken)=="fixture"
        assert agent._on_usage is None


def test_observer_cleared_after_turn_error(tmp_path,monkeypatch):
    monkeypatch.setenv("ZELINE_HOME",str(tmp_path/"home"))
    module=importlib.import_module("zeline.agent")
    agent=module.Zeline(identity="eval:usage-error",tool_profile="safe",workspace=str(tmp_path))
    with mock.patch.object(agent,"_call_llm",side_effect=RuntimeError("fixture")):
        with pytest.raises(RuntimeError):
            agent.send("fixture",on_usage=lambda event: None)
    assert agent._on_usage is None


@pytest.mark.parametrize("protocol,with_usage", [("openai",True),("openai",False),("anthropic",True),("anthropic",False)])
def test_stream_observer_reports_missing_and_zero(protocol,with_usage):
    import json
    module=importlib.import_module("zeline.agent")
    agent=module.Zeline.__new__(module.Zeline)
    agent.protocol=protocol
    agent.model="fixture"
    agent.identity="eval:stream"
    events=[]
    agent._on_usage=events.append
    if protocol == "openai":
        messages=[{"choices":[{"delta":{"content":"fixture"}}]}]
        if with_usage:
            messages.append({"usage":{"prompt_tokens":0,"completion_tokens":0},"choices":[]})
        lines=["data: "+json.dumps(m) for m in messages]+["data: [DONE]"]
        consume=agent._consume_openai_stream
    else:
        messages=[{"type":"content_block_delta","delta":{"type":"text_delta","text":"fixture"}}]
        if with_usage:
            messages.insert(0,{"type":"message_start","message":{"usage":{"input_tokens":0}}})
            messages.append({"type":"message_delta","usage":{"output_tokens":0}})
        messages.append({"type":"message_stop"})
        lines=["data: "+json.dumps(m) for m in messages]
        consume=agent._consume_anthropic_stream
    response=mock.Mock()
    with mock.patch.object(agent,"_stream_lines",return_value=iter(lines)), mock.patch.object(agent,"_usage_store",return_value=mock.Mock()):
        result=consume(response)
    assert result["content"] == "fixture"
    assert len(events)==1
    expected=0 if with_usage else None
    assert events[0] == dict(input_tokens=expected,output_tokens=expected,total_tokens=expected)
    response.close.assert_called_once()
