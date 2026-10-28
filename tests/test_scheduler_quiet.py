"""Opt-in no-update delivery semantics, using disposable scheduler state."""
import pytest
from types import SimpleNamespace
import time


@pytest.fixture
def cron(tmp_path, monkeypatch):
    from zeline import config, scheduler

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "state")
    # New feature tests must never reach a configured provider or real chat.
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("network forbidden in quiet scheduler tests")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    return scheduler


def test_quiet_opt_in_persists_without_changing_legacy_jobs(cron):
    legacy = cron.add_job("1h", "Report project changes")
    assert getattr(legacy, "quiet_if_unchanged", None) is False
    quiet = cron.add_job("1h", "Report project changes", quiet_if_unchanged=True)
    assert cron.find_job(quiet.id).quiet_if_unchanged is True
    assert cron.find_job(legacy.id).quiet_if_unchanged is False


def test_opted_in_exact_marker_is_audited_without_delivery_or_context_handoff(cron, monkeypatch):
    job = cron.add_job("1h", "Report changes", quiet_if_unchanged=True)
    deliveries = []
    monkeypatch.setattr(cron, "deliver", lambda *args: deliveries.append(args) or (True, "fixture"))
    sessions = SimpleNamespace(send=lambda **kwargs: "  __ZELINE_NO_UPDATE__\n")
    runner = cron.Scheduler(sessions)
    monkeypatch.setattr(runner, "_run_agent", lambda item: "  __ZELINE_NO_UPDATE__\n")
    runner._execute(job, time.time())
    assert deliveries == []
    stored = cron.find_job(job.id)
    assert stored.last_status == "ok: no update (delivery suppressed)"
    assert stored.runs == 1 and stored.failures == 0
    assert stored.last_output == ""  # Control markers are not evidence for another job.
    outputs = list(cron.output_dir().glob("*.md"))
    assert len(outputs) == 1
    assert "__ZELINE_NO_UPDATE__" in outputs[0].read_text()


@pytest.mark.parametrize("value", [1, 0, "true", "false", None, [], {}])
def test_quiet_setting_rejects_non_boolean_values(cron, value):
    with pytest.raises(cron.CronError, match="quiet_if_unchanged must be a boolean"):
        cron.add_job("1h", "Report changes", quiet_if_unchanged=value)
    assert cron.list_jobs() == []


def test_script_only_cannot_opt_into_agent_control_marker(cron, tmp_path):
    script = tmp_path / "fixture.py"
    script.write_text("print('fixture')\n")
    with pytest.raises(cron.CronError, match="agent-only"):
        cron.add_job("1h", script=str(script), no_agent=True, quiet_if_unchanged=True)
    assert cron.list_jobs() == []


def test_quiet_job_has_generation_scoped_agent_and_explicit_prompt_contract(cron, monkeypatch):
    from zeline import agent
    from unittest.mock import Mock

    nodes = []
    class FixtureAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model = "fixture-default-model"
            self.executor = SimpleNamespace()
            self.send = Mock(return_value="__ZELINE_NO_UPDATE__")
            nodes.append(self)

    monkeypatch.setattr(agent, "Zeline", FixtureAgent)
    sessions = SimpleNamespace(send=Mock(return_value="legacy fixture"))
    job = cron.add_job("1h", "Report changes", quiet_if_unchanged=True)
    result = cron.Scheduler(sessions)._run_agent(job)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.kwargs["identity"] == f"cron:{job.id}:{job.generation}"
    assert "__ZELINE_NO_UPDATE__" in node.kwargs["system_extra"]
    assert "errors" in node.kwargs["system_extra"].lower()
    assert node.model == "fixture-default-model"
    assert node.executor._model_override is None
    assert result == "__ZELINE_NO_UPDATE__"
    sessions.send.assert_not_called()


def test_quiet_execution_exception_still_delivers_an_error_alert(cron, monkeypatch):
    job = cron.add_job("1h", "Report changes", quiet_if_unchanged=True)
    runner = cron.Scheduler(SimpleNamespace())
    def failure(item):
        raise RuntimeError("controlled offline failure")
    monkeypatch.setattr(runner, "_run_agent", failure)
    deliveries = []
    monkeypatch.setattr(cron, "deliver", lambda *args: deliveries.append(args) or (True, "fixture"))
    runner._execute(job, time.time())
    assert len(deliveries) == 1
    assert "controlled offline failure" in deliveries[0][1]
    stored = cron.find_job(job.id)
    assert stored.last_status.startswith("error:")
    assert stored.failures == 1 and stored.last_output == ""


def test_cli_can_create_and_show_explicit_quiet_job(cron, capsys):
    from zeline import cli

    args = ["cron", "add", "1h", "Report changes", "--quiet-if-unchanged"]
    namespace = cli.build_parser().parse_args(args)
    assert namespace.quiet_if_unchanged is True
    assert cli.main(args) == 0
    stored = cron.list_jobs()
    assert len(stored) == 1 and stored[0].quiet_if_unchanged is True
    assert cli.cmd_cron("show", job_id=stored[0].id) == 0
    assert "quiet if unchanged: on" in capsys.readouterr().out


def test_script_output_never_uses_agent_marker_even_if_persisted_flag_is_malformed(cron, monkeypatch, tmp_path):
    script = tmp_path / "fixture.py"
    script.write_text("print('__ZELINE_NO_UPDATE__')\n")
    job = cron.add_job("1h", script=str(script), no_agent=True)
    job.quiet_if_unchanged = True  # Defensive path for manually edited/newer state.
    deliveries = []
    monkeypatch.setattr(cron, "deliver", lambda *args: deliveries.append(args) or (True, "fixture"))
    cron.Scheduler(SimpleNamespace())._execute(job, time.time())
    assert len(deliveries) == 1
    assert deliveries[0][1].strip() == "__ZELINE_NO_UPDATE__"


@pytest.mark.parametrize("quiet,reply", [
    (False, "__ZELINE_NO_UPDATE__"),
    (True, "__SILENT__"),
    (True, "Update: __ZELINE_NO_UPDATE__"),
    (True, "__ZELINE_NO_UPDATE__ followed by actual changes"),
    (True, "```__ZELINE_NO_UPDATE__```"),
    (True, "ERROR: source unavailable __ZELINE_NO_UPDATE__"),
    (True, "An actual project update"),
])
def test_only_exact_opted_in_marker_suppresses_delivery(cron, monkeypatch, quiet, reply):
    job = cron.add_job("1h", "Report changes", quiet_if_unchanged=quiet)
    runner = cron.Scheduler(SimpleNamespace())
    monkeypatch.setattr(runner, "_run_agent", lambda item: reply)
    deliveries = []
    monkeypatch.setattr(cron, "deliver", lambda *args: deliveries.append(args) or (True, "fixture"))
    runner._execute(job, time.time())
    assert len(deliveries) == 1
    stored = cron.find_job(job.id)
    if reply.startswith("ERROR"):
        assert stored.failures == 1 and stored.last_output == ""
    else:
        assert stored.failures == 0 and stored.last_output


def test_jobs_written_before_quiet_option_keep_legacy_delivery(cron):
    import json
    job = cron.add_job("1h", "Report changes")
    path = cron.jobs_path()
    raw = json.loads(path.read_text())
    del raw[0]["quiet_if_unchanged"]
    path.write_text(json.dumps(raw))
    assert cron.find_job(job.id).quiet_if_unchanged is False


def test_malformed_truthy_flag_cannot_silence_delivery(cron, monkeypatch):
    job = cron.add_job("1h", "Report changes")
    job.quiet_if_unchanged = "true"
    runner = cron.Scheduler(SimpleNamespace())
    monkeypatch.setattr(runner, "_run_agent", lambda item: "__ZELINE_NO_UPDATE__")
    deliveries = []
    monkeypatch.setattr(cron, "deliver", lambda *args: deliveries.append(args) or (True, "fixture"))
    runner._execute(job, time.time())
    assert len(deliveries) == 1


def test_quiet_result_cannot_be_forwarded_as_upstream_evidence(cron, monkeypatch):
    source = cron.add_job("1h", "Report changes", quiet_if_unchanged=True)
    runner = cron.Scheduler(SimpleNamespace())
    monkeypatch.setattr(runner, "_run_agent", lambda item: "__ZELINE_NO_UPDATE__")
    runner._execute(source, time.time())
    consumer = cron.add_job("1h", "Summarize the producer", context_from=[source.id])
    with pytest.raises(cron.CronError, match="no successful latest result"):
        cron.Scheduler(SimpleNamespace())._run_agent(consumer)
    stored = cron.find_job(source.id)
    assert stored.next_run > stored.last_run


def test_quiet_job_cannot_reuse_a_legacy_generationless_agent_identity(cron, monkeypatch):
    from zeline import agent
    monkeypatch.setattr(agent, "Zeline", lambda **kwargs: SimpleNamespace(
        model="fixture-model", executor=SimpleNamespace(), send=lambda *a, **kw: "fixture"))
    job = cron.Job(id="job1", schedule="1h", prompt="fixture", quiet_if_unchanged=True)
    with pytest.raises(cron.CronError, match="generation"):
        cron.Scheduler(SimpleNamespace())._run_agent(job)
