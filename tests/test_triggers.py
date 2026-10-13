"""Tests for zeline.triggers — all connectors are mocked, no real API calls."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import triggers
from zeline.triggers import TriggerError


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeGmail:
    """Duck-typed stand-in for the google connector's Gmail surface."""

    def __init__(self, messages):
        # each message: {id (hex), internalDate (ms epoch), from, subject, date, snippet}
        self.messages = list(messages)
        self.calls = []

    def _api(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("params")))
        if url.endswith("/messages"):
            return {"messages": [{"id": m["id"]} for m in self.messages]}
        mid = url.rsplit("/", 1)[-1]
        for m in self.messages:
            if m["id"] == mid:
                return {
                    "id": mid,
                    "internalDate": str(m["internalDate"]),
                    "snippet": m.get("snippet", ""),
                    "payload": {
                        "headers": [
                            {"name": "From", "value": m.get("from", "")},
                            {"name": "Subject", "value": m.get("subject", "")},
                            {"name": "Date", "value": m.get("date", "")},
                        ]
                    },
                }
        raise AssertionError(f"unknown message id {mid}")


class FakeGitHub:
    """Duck-typed stand-in for the github connector."""

    def __init__(self, issues=(), prs=()):
        self.issues = list(issues)
        self.prs = list(prs)
        self.calls = []

    def _api(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("params")))
        if path.endswith("/issues"):
            return self.issues
        if path.endswith("/pulls"):
            return self.prs
        raise AssertionError(f"unexpected path {path}")


def gh_issue(num, title, labels=(), created=None, is_pr=False):
    # The trigger state below starts one hour in the past. Keep synthetic
    # GitHub events relative to the test run; a fixed calendar timestamp turns
    # this into a time bomb once that date is more than an hour old.
    created = created or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    item = {
        "id": 1000 + num,
        "number": num,
        "title": title,
        "html_url": f"https://github.com/o/r/issues/{num}",
        "created_at": created,
        "labels": [{"name": lb} for lb in labels],
        "body": f"body of {title}",
    }
    if is_pr:
        item["pull_request"] = {"url": "x"}
    return item


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZELINE_HOME", str(tmp_path))
    triggers._CONNECTOR_OVERRIDES.clear()
    yield tmp_path
    triggers._CONNECTOR_OVERRIDES.clear()


def _gmail_trigger(**kw):
    args = dict(
        connector="gmail",
        event_type="new_email",
        filt={},
        action="notify",
        action_params={"text_template": "New mail: {subject} from {from}"},
        interval_min=5,
    )
    args.update(kw)
    return triggers.add_trigger(**args)


def _seed_state(home, tid, last_ts, seen=(), checked_at=0.0):
    path = home / "trigger_state.json"
    state = {}
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
    state[tid] = {
        "last_id": "",
        "last_ts": last_ts,
        "checked_at": checked_at,
        "baselined": True,
        "seen_ids": list(seen),
        "failed": {},
    }
    path.write_text(json.dumps(state), encoding="utf-8")


def _force_due(home, tid):
    path = home / "trigger_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state[tid]["checked_at"] = 0.0
    path.write_text(json.dumps(state), encoding="utf-8")


def _load_state(home):
    return json.loads((home / "trigger_state.json").read_text(encoding="utf-8"))


NOW_MS = int(time.time() * 1000)


# --------------------------------------------------------------------------
# (a) add / list / remove
# --------------------------------------------------------------------------

def test_add_list_remove(home):
    t = _gmail_trigger(trigger_id="my-trg_1")
    assert t.id == "my-trg_1"
    assert t.interval_min == 5 and t.enabled is True

    listed = triggers.list_triggers()
    assert [x.id for x in listed] == ["my-trg_1"]

    assert triggers.remove_trigger("my-trg_1") is True
    assert triggers.list_triggers() == []
    assert triggers.remove_trigger("my-trg_1") is False


def test_add_auto_id_and_0600(home):
    t = _gmail_trigger()
    assert t.id.startswith("trg_")
    mode = stat.S_IMODE((home / "triggers.json").stat().st_mode)
    assert mode == 0o600


def test_add_duplicate_id_rejected(home):
    _gmail_trigger(trigger_id="dup")
    with pytest.raises(TriggerError):
        _gmail_trigger(trigger_id="dup")


def test_interval_clamped(home):
    assert _gmail_trigger(trigger_id="a", interval_min=0).interval_min == 1
    assert _gmail_trigger(trigger_id="b", interval_min=99999).interval_min == 1440


# --------------------------------------------------------------------------
# (b) poll fires for new items, no double-fire
# --------------------------------------------------------------------------

def test_poll_fires_new_and_no_double_fire(home, monkeypatch):
    seen_texts = []
    monkeypatch.setattr(triggers, "notify_owner", seen_texts.append)

    tid = _gmail_trigger(trigger_id="t1").id
    fake = FakeGmail([
        {"id": "aaa111", "internalDate": NOW_MS - 100_000,
         "from": "a@x.com", "subject": "first", "date": "today"},
    ])
    triggers._CONNECTOR_OVERRIDES["gmail"] = fake

    # first poll: baseline only, fires nothing
    assert triggers.poll_once() == [{"trigger_id": "t1", "fired": 0}]
    assert seen_texts == []

    # a new message arrives
    fake.messages.append(
        {"id": "bbb222", "internalDate": NOW_MS - 5_000,
         "from": "b@x.com", "subject": "second", "date": "today"}
    )
    _force_due(home, "t1")
    res = triggers.poll_once()
    assert res == [{"trigger_id": "t1", "fired": 1}]
    assert seen_texts == ["New mail: second from b@x.com"]

    # second poll: nothing new -> no double fire
    _force_due(home, "t1")
    assert triggers.poll_once() == [{"trigger_id": "t1", "fired": 0}]
    assert seen_texts == ["New mail: second from b@x.com"]


def test_run_loop_once_returns_results(home, monkeypatch):
    monkeypatch.setattr(triggers, "notify_owner", lambda text: None)
    _gmail_trigger(trigger_id="t1")
    triggers._CONNECTOR_OVERRIDES["gmail"] = FakeGmail([])
    res = triggers.run_loop(once=True)
    assert res == [{"trigger_id": "t1", "fired": 0}]


# --------------------------------------------------------------------------
# (c) filters work
# --------------------------------------------------------------------------

def test_gmail_filters(home, monkeypatch):
    fired = []
    monkeypatch.setattr(triggers, "notify_owner", fired.append)
    _gmail_trigger(
        trigger_id="t1",
        filt={"from_contains": "boss@corp.com", "subject_contains": "urgent"},
    )
    triggers._CONNECTOR_OVERRIDES["gmail"] = FakeGmail([
        {"id": "a1b2c3", "internalDate": NOW_MS - 60_000,
         "from": "Boss <boss@corp.com>", "subject": "URGENT: deploy now", "date": "t"},
        {"id": "d4e5f6", "internalDate": NOW_MS - 50_000,
         "from": "spam@evil.com", "subject": "URGENT: you won", "date": "t"},
        {"id": "a7b8c9", "internalDate": NOW_MS - 40_000,
         "from": "boss@corp.com", "subject": "lunch plans", "date": "t"},
    ])
    _seed_state(home, "t1", time.time() - 3600)
    assert triggers.poll_once() == [{"trigger_id": "t1", "fired": 1}]
    assert len(fired) == 1 and "deploy now" in fired[0]


def test_github_issue_filter_labels_and_pr_exclusion(home, monkeypatch):
    fired = []
    monkeypatch.setattr(triggers, "notify_owner", fired.append)
    triggers.add_trigger(
        connector="github",
        event_type="new_issue",
        filt={"repo": "o/r", "labels": ["bug"]},
        action="notify",
        action_params={"text_template": "#{number} {title} [{labels}]"},
        trigger_id="gh1",
    )
    triggers._CONNECTOR_OVERRIDES["github"] = FakeGitHub(
        issues=[
            gh_issue(1, "crash on start", labels=["bug"]),
            gh_issue(2, "docs typo", labels=["docs"]),
            gh_issue(3, "pr disguised", labels=["bug"], is_pr=True),
        ]
    )
    _seed_state(home, "gh1", time.time() - 3600)
    assert triggers.poll_once() == [{"trigger_id": "gh1", "fired": 1}]
    assert fired == ["#1 crash on start [bug]"]


def test_github_new_pr(home, monkeypatch):
    fired = []
    monkeypatch.setattr(triggers, "notify_owner", fired.append)
    triggers.add_trigger(
        connector="github",
        event_type="new_pr",
        filt={"repo": "o/r"},
        action="notify",
        action_params={"text_template": "PR #{number}: {title}"},
        trigger_id="gh2",
    )
    pr = dict(gh_issue(7, "add feature"))
    pr["html_url"] = "https://github.com/o/r/pull/7"
    triggers._CONNECTOR_OVERRIDES["github"] = FakeGitHub(prs=[pr])
    _seed_state(home, "gh2", time.time() - 3600)
    assert triggers.poll_once() == [{"trigger_id": "gh2", "fired": 1}]
    assert fired == ["PR #7: add feature"]


def test_gmail_query_uses_plain_newer_than(home):
    """Filter text must never reach the Gmail query (no query injection)."""
    _gmail_trigger(
        trigger_id="t1",
        filt={"from_contains": 'a" OR b', "subject_contains": "x"},
    )
    fake = FakeGmail([])
    triggers._CONNECTOR_OVERRIDES["gmail"] = fake
    triggers.poll_once()
    for _m, _u, params in fake.calls:
        assert params["q"].startswith("newer_than:")
        assert "OR" not in params["q"]


# --------------------------------------------------------------------------
# (d) state persists atomically
# --------------------------------------------------------------------------

def test_state_persist_atomic(home, monkeypatch):
    monkeypatch.setattr(triggers, "notify_owner", lambda text: None)
    _gmail_trigger(trigger_id="t1")
    triggers._CONNECTOR_OVERRIDES["gmail"] = FakeGmail([
        {"id": "aa11bb", "internalDate": NOW_MS - 10_000,
         "from": "a@x.com", "subject": "hi", "date": "t"},
    ])
    triggers.poll_once()  # baseline
    state = _load_state(home)["t1"]
    assert state["baselined"] is True
    assert state["seen_ids"] == ["aa11bb"]
    assert state["last_ts"] > 0
    assert state["checked_at"] > 0
    # no temp files left behind
    assert list(home.glob("*.tmp-*")) == []
    assert stat.S_IMODE((home / "trigger_state.json").stat().st_mode) == 0o600


# --------------------------------------------------------------------------
# (e) invalid input rejected
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["has space", "semi;colon", "x" * 65, "", "a/b"])
def test_invalid_trigger_id_rejected(home, bad_id):
    with pytest.raises(TriggerError):
        _gmail_trigger(trigger_id=bad_id)


def test_invalid_connector_event_action_rejected(home):
    with pytest.raises(TriggerError):
        _gmail_trigger(connector="slack")
    with pytest.raises(TriggerError):
        _gmail_trigger(event_type="new_pr")  # gmail has no new_pr
    with pytest.raises(TriggerError):
        _gmail_trigger(action="self_destruct")


def test_invalid_filter_rejected(home):
    with pytest.raises(TriggerError):
        _gmail_trigger(filt={"bogus_key": "x"})
    with pytest.raises(TriggerError):
        triggers.add_trigger(
            connector="github", event_type="new_issue",
            filt={"repo": "not a repo!!!"}, action="notify",
            action_params={"text_template": "hi"},
        )
    with pytest.raises(TriggerError):
        triggers.add_trigger(
            connector="github", event_type="new_issue",
            filt={"repo": "o/r", "labels": "bug"},  # must be a list
            action="notify", action_params={"text_template": "hi"},
        )


def test_invalid_template_rejected(home):
    with pytest.raises(TriggerError):
        _gmail_trigger(action_params={"text_template": "unclosed {subject"})
    with pytest.raises(TriggerError):
        _gmail_trigger(action_params={})  # missing text_template
    # no attribute access / format-spec tricks in templates
    with pytest.raises(TriggerError):
        _gmail_trigger(action_params={"text_template": "{subject.__class__}"})
    with pytest.raises(TriggerError):
        _gmail_trigger(action_params={"text_template": "{subject[0]}"})


def test_tool_handler_rejects_bad_json(home):
    out = triggers.TOOL_HANDLERS["trigger_add"](
        connector="gmail",
        event_type="new_email",
        filter_json="{not json",
        action="notify",
        action_params_json="{}",
    )
    assert out.startswith("ERROR:")


# --------------------------------------------------------------------------
# (f) a failing action does not break other triggers; bounded retries
# --------------------------------------------------------------------------

def test_failing_action_isolated_and_retried_then_skipped(home, monkeypatch):
    calls = []

    def flaky_notify(text):
        if "FAILME" in text:
            raise RuntimeError("delivery down")
        calls.append(text)

    monkeypatch.setattr(triggers, "notify_owner", flaky_notify)

    _gmail_trigger(trigger_id="bad",
                   filt={"subject_contains": "failme"},
                   action_params={"text_template": "boom FAILME {subject}"})
    _gmail_trigger(trigger_id="good",
                   filt={"subject_contains": "hello"},
                   action_params={"text_template": "ok {subject}"})
    triggers._CONNECTOR_OVERRIDES["gmail"] = FakeGmail([
        {"id": "f00f11", "internalDate": NOW_MS - 60_000,
         "from": "a@x.com", "subject": "FAILME please", "date": "t"},
        {"id": "beef22", "internalDate": NOW_MS - 50_000,
         "from": "b@x.com", "subject": "hello world", "date": "t"},
    ])
    _seed_state(home, "bad", time.time() - 3600)
    _seed_state(home, "good", time.time() - 3600)

    # attempt 1: bad fails (retried later), good fires for its match only
    res = triggers.poll_once()
    assert {"trigger_id": "bad", "fired": 0} in res
    assert {"trigger_id": "good", "fired": 1} in res
    assert calls == ["ok hello world"]
    assert _load_state(home)["bad"]["failed"] == {"f00f11": 1}

    # attempts 2 and 3: still failing, good stays quiet (no double fire)
    for _ in range(2):
        _force_due(home, "bad")
        _force_due(home, "good")
        res = triggers.poll_once()
        assert {"trigger_id": "bad", "fired": 0} in res
        assert {"trigger_id": "good", "fired": 0} in res
    assert calls == ["ok hello world"]

    # after 3 attempts the item is skipped, not retried forever
    state = _load_state(home)["bad"]
    assert state["failed"] == {}
    assert "f00f11" in state["seen_ids"]
    _force_due(home, "bad")
    _force_due(home, "good")
    assert triggers.poll_once() == [
        {"trigger_id": "bad", "fired": 0},
        {"trigger_id": "good", "fired": 0},
    ]


def test_fetch_failure_reports_error_and_keeps_loop_alive(home, monkeypatch):
    monkeypatch.setattr(triggers, "notify_owner", lambda text: None)

    class Exploding:
        def _api(self, *a, **k):
            raise RuntimeError("network down")

    _gmail_trigger(trigger_id="down")
    triggers._CONNECTOR_OVERRIDES["gmail"] = Exploding()

    res = triggers.poll_once()
    assert res[0]["trigger_id"] == "down"
    assert res[0]["fired"] == 0
    assert "network down" in res[0]["error"]
    # a failed first fetch must NOT mark the trigger baselined...
    assert _load_state(home)["down"].get("baselined") is not True

    # ...so when the connector recovers, the first good poll baselines quietly
    triggers._CONNECTOR_OVERRIDES["gmail"] = FakeGmail([
        {"id": "cc33dd", "internalDate": NOW_MS - 10_000,
         "from": "a@x.com", "subject": "hi", "date": "t"},
    ])
    _force_due(home, "down")
    assert triggers.poll_once() == [{"trigger_id": "down", "fired": 0}]
    assert _load_state(home)["down"]["baselined"] is True

    with pytest.raises(TriggerError):
        triggers.fetch_new("nope", "new_email", {}, None)


# --------------------------------------------------------------------------
# actions: outbox, workflow hook, memory note
# --------------------------------------------------------------------------

def test_default_notify_writes_outbox(home):
    triggers.notify_owner("hello owner")
    lines = (home / "trigger_outbox.jsonl").read_text(encoding="utf-8").strip().split("\n")
    entry = json.loads(lines[-1])
    assert entry["kind"] == "notify" and entry["text"] == "hello owner"
    assert stat.S_IMODE((home / "trigger_outbox.jsonl").stat().st_mode) == 0o600


def test_default_run_workflow_writes_outbox(home):
    triggers.run_workflow("wf_1", {"trigger_id": "t1"})
    lines = (home / "trigger_outbox.jsonl").read_text(encoding="utf-8").strip().split("\n")
    entry = json.loads(lines[-1])
    assert entry["kind"] == "run_workflow" and entry["workflow_id"] == "wf_1"


def test_run_workflow_action_end_to_end(home, monkeypatch):
    seen = []
    monkeypatch.setattr(triggers, "run_workflow",
                        lambda wid, ctx: seen.append((wid, ctx)))
    triggers.add_trigger(
        connector="github", event_type="new_issue", filt={"repo": "o/r"},
        action="run_workflow", action_params={"workflow_id": "wf_9"},
        trigger_id="gh3",
    )
    triggers._CONNECTOR_OVERRIDES["github"] = FakeGitHub(
        issues=[gh_issue(4, "needs triage")]
    )
    _seed_state(home, "gh3", time.time() - 3600)
    assert triggers.poll_once() == [{"trigger_id": "gh3", "fired": 1}]
    assert seen[0][0] == "wf_9"
    assert seen[0][1]["trigger_id"] == "gh3"
    assert seen[0][1]["event"]["title"] == "needs triage"


def test_append_memory_action(home):
    triggers.add_trigger(
        connector="github", event_type="new_issue", filt={"repo": "o/r"},
        action="append_memory", action_params={"note_prefix": "GH"},
        trigger_id="gh4",
    )
    triggers._CONNECTOR_OVERRIDES["github"] = FakeGitHub(
        issues=[gh_issue(5, "memory test")]
    )
    _seed_state(home, "gh4", time.time() - 3600)
    assert triggers.poll_once() == [{"trigger_id": "gh4", "fired": 1}]
    inbox = home / "memory" / "inbox"
    files = list(inbox.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "GH: memory test" in text and "trigger gh4" in text


def test_template_sanitises_hostile_subject(home):
    nasty = "line1\nline2\r\n\t" + "A" * 500
    out = triggers._render_template("Sub: {subject}", {"subject": nasty})
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert len(out) < 400  # truncated
    # unknown placeholder -> empty, not KeyError
    assert triggers._render_template("x{nope}y", {}) == "xy"


# --------------------------------------------------------------------------
# tools + CLI surface
# --------------------------------------------------------------------------

def test_tool_defs_and_handlers(home):
    defs = triggers.get_tool_defs()
    assert {d.name for d in defs} == {"trigger_add", "trigger_list", "trigger_remove"}
    assert set(triggers.TOOL_HANDLERS) == {"trigger_add", "trigger_list", "trigger_remove"}
    for d in defs:
        d.schema()  # must not raise
    assert "No triggers" in triggers.TOOL_HANDLERS["trigger_list"]()
    out = triggers.TOOL_HANDLERS["trigger_add"](
        connector="gmail", event_type="new_email",
        filter_json='{"from_contains": "a@b.c"}', action="notify",
        action_params_json='{"text_template": "hi {subject}"}',
        interval_min=10, trigger_id="tool1",
    )
    assert "tool1" in out
    assert "tool1" in triggers.TOOL_HANDLERS["trigger_list"]()
    assert "removed" in triggers.TOOL_HANDLERS["trigger_remove"](trigger_id="tool1")


def test_register_cli(home, capsys):
    parser = argparse.ArgumentParser(prog="zeline")
    sub = parser.add_subparsers(dest="command")
    triggers.register_cli(sub)

    args = parser.parse_args(["trigger", "list"])
    assert args.func(args) == 0
    assert "No triggers" in capsys.readouterr().out

    args = parser.parse_args([
        "trigger", "add", "--connector", "gmail", "--event", "new_email",
        "--filter", '{"from_contains": "x@y.z"}',
        "--action", "notify",
        "--action-params", '{"text_template": "Mail: {subject}"}',
    ])
    assert args.func(args) == 0
    assert "Added trigger trg_" in capsys.readouterr().out

    args = parser.parse_args(["trigger", "add", "--connector", "gmail",
                              "--event", "new_email", "--filter", "{bad",
                              "--action", "notify"])
    assert args.func(args) == 1
    assert "ERROR" in capsys.readouterr().out


def test_cli_test_dry_run(home, capsys, monkeypatch):
    monkeypatch.setattr(triggers, "notify_owner",
                        lambda text: pytest.fail("must not fire in dry-run"))
    _gmail_trigger(trigger_id="t1")
    triggers._CONNECTOR_OVERRIDES["gmail"] = FakeGmail([
        {"id": "dd44ee", "internalDate": NOW_MS - 10_000,
         "from": "a@x.com", "subject": "dry", "date": "t"},
    ])
    parser = argparse.ArgumentParser(prog="zeline")
    sub = parser.add_subparsers(dest="command")
    triggers.register_cli(sub)

    args = parser.parse_args(["trigger", "test", "t1"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "no baseline yet" in out  # first run: nothing would fire
    # state file untouched by dry-run
    assert not (home / "trigger_state.json").exists()
