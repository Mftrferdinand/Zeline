"""Tests for the json_query tool."""
import json
from pathlib import Path

from zeline import tools

DOC = json.dumps({
    "users": [
        {"name": "ana", "age": 30, "active": True},
        {"name": "budi", "age": 17, "active": False},
        {"name": "citra", "age": 45, "active": True},
    ],
    "meta": {"total": 3, "page": 1},
})

WS = Path(".")


def test_dot_path():
    out = tools._json_query(DOC, "meta.total", WS)
    assert json.loads(out) == 3


def test_index_and_wildcard():
    out = tools._json_query(DOC, "users.0.name", WS)
    assert json.loads(out) == "ana"
    out = tools._json_query(DOC, "users.*.name", WS)
    assert json.loads(out) == ["ana", "budi", "citra"]


def test_slice():
    out = tools._json_query(DOC, "users.0:2.name", WS)
    assert json.loads(out) == ["ana", "budi"]


def test_comparison_filter():
    out = tools._json_query(DOC, "users | age>=30 | name", WS)
    assert json.loads(out) == ["ana", "citra"]
    out = tools._json_query(DOC, "users | active==true | name", WS)
    assert json.loads(out) == ["ana", "citra"]


def test_string_equality():
    out = tools._json_query(DOC, 'users | name==citra | age', WS)
    assert json.loads(out) == [45]


def test_workspace_file_input(tmp_path: Path):
    target = tmp_path / "data.json"
    target.write_text(DOC, encoding="utf-8")
    out = tools._json_query(str(target), "meta.page", tmp_path)
    assert json.loads(out) == 1


def test_workspace_file_path_escape_blocked(tmp_path: Path):
    out = tools._json_query("/etc/hostname", "x", tmp_path)
    assert out.startswith("ERROR")


def test_invalid_json():
    assert tools._json_query("{not json", "a", WS).startswith("ERROR")


def test_missing_key_is_error_not_crash():
    assert tools._json_query(DOC, "meta.nope", WS).startswith("ERROR")


def test_empty_query_returns_doc():
    out = tools._json_query('{"a":1}', "", WS)
    assert json.loads(out) == {"a": 1}


def test_result_truncated():
    big = json.dumps({"blob": "x" * 20000})
    out = tools._json_query(big, "blob", WS)
    assert out.endswith("... [truncated]")


def test_no_eval_execution():
    # Query stages never execute code: strings stay strings.
    out = tools._json_query('{"a": {"__import__": 1}}', "a.__import__", WS)
    assert json.loads(out) == 1
