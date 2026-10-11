"""Test cost telemetry: journal, pricing, summary, handlers."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zeline import cost
from zeline.cost import CostError


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    # Journal selalu ke tmp_path; override harga runtime selalu direset.
    monkeypatch.setattr(cost, "_JOURNAL_OVERRIDE", tmp_path / "cost_journal.jsonl")
    cost.reset_price_overrides()
    yield
    cost.reset_price_overrides()
    monkeypatch.undo()


def _row(**kw):
    base = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "identity": "cli:local",
        "kind": "llm",
        "tool_name": "__llm__",
        "model": "gpt-4o",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cost_usd": 0.0,
        "duration_ms": 100.0,
    }
    base.update(kw)
    return base


def _write_rows(path: Path, rows: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# ------------------------------------------------------------ kalkulasi biaya


def test_cost_calc_from_builtin_table():
    # gpt-4o estimasi: 2.5 in / 10.0 out per 1M
    got = cost.cost_usd("gpt-4o", 1_000_000, 1_000_000)
    assert got == pytest.approx(12.5)


def test_cost_calc_unknown_model_is_zero_not_guessed():
    assert cost.cost_usd("model-tidak-ada-xyz", 10_000, 5_000) == 0.0


def test_cost_calc_never_nan():
    assert cost.cost_usd("gpt-4o", -5, -10) == 0.0  # negatif dijepit
    assert cost.cost_usd("", 100, 100) == 0.0
    assert cost.cost_usd(None, 100, 100) == 0.0


def test_price_prefix_match():
    # gpt-4o menutupi varian bertanggal
    assert cost.price_for("gpt-4o-2024-08-06") == (2.5, 10.0)


def test_price_override_runtime_wins():
    cost.set_model_prices({"gpt-4o": (100.0, 200.0)})
    assert cost.cost_usd("gpt-4o", 1_000_000, 0) == pytest.approx(100.0)


def test_price_override_config(monkeypatch):
    monkeypatch.setattr(
        cost.config, "MODEL_PRICES", {"gpt-4o": {"input": 50.0, "output": 60.0}}, raising=False
    )
    assert cost.cost_usd("gpt-4o", 1_000_000, 0) == pytest.approx(50.0)


def test_price_override_env(monkeypatch):
    monkeypatch.setenv("ZELINE_PRICE_GPT_4O", "7.5,8.5")
    assert cost.price_for("gpt-4o") == (7.5, 8.5)


def test_price_env_invalid_ignored(monkeypatch):
    monkeypatch.setenv("ZELINE_PRICE_GPT_4O", "bukan-angka")
    assert cost.price_for("gpt-4o") == (2.5, 10.0)  # fallback estimasi


def test_set_model_prices_ignores_invalid():
    cost.set_model_prices({"gpt-4o": ("x", "y"), "ok-model": (1.0, 2.0), "neg": (-1.0, 2.0)})
    assert cost.price_for("ok-model") == (1.0, 2.0)
    assert cost.price_for("gpt-4o") == (2.5, 10.0)  # tidak tertimpa


# ------------------------------------------------------------ record & journal


def test_record_llm_then_read_back(tmp_path):
    path = tmp_path / "j.jsonl"
    assert cost.record_llm_call("user-1", "gpt-4o", 2000, 1000, 250.0, journal_path=path) is True
    rows = list(cost.iter_journal(path))
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "llm"
    assert row["tool_name"] == "__llm__"
    assert row["identity"] == "user-1"
    assert row["model"] == "gpt-4o"
    assert row["input_tokens"] == 2000
    assert row["output_tokens"] == 1000
    assert row["duration_ms"] == pytest.approx(250.0)
    assert row["cost_usd"] == pytest.approx(0.015)  # 2k*2.5 + 1k*10 / 1M
    assert "timestamp" in row


def test_record_tool_call(tmp_path):
    path = tmp_path / "j.jsonl"
    assert cost.record_tool_call("user-1", "web_search", "gpt-4o", 120.0, journal_path=path) is True
    rows = list(cost.iter_journal(path))
    assert rows[0]["kind"] == "tool"
    assert rows[0]["tool_name"] == "web_search"
    assert rows[0]["input_tokens"] == 0


def test_record_never_raises_when_unwritable(tmp_path):
    # journal_path menunjuk ke sebuah DIREKTORI -> open("a") gagal.
    bad = tmp_path / "as_dir"
    bad.mkdir()
    assert cost.record_llm_call("u", "gpt-4o", 1, 1, 1.0, journal_path=bad) is False
    assert cost.record_tool_call("u", "t", "gpt-4o", 1.0, journal_path=bad) is False
    # ...dan tanpa param pun tidak raise (pakai override fixture yang valid,
    # jadi paksa lewat path read-only dengan menghapus write permission
    # tidak portabel; cukup pastikan fungsi membungkus segalanya).
    assert cost.record_llm_call(None, None, "x", "y", "z") in (True, False)


def test_record_never_raises_on_garbage_types(tmp_path):
    path = tmp_path / "j.jsonl"
    # Tipe sampah tidak boleh meledak — dijepit ke nilai aman.
    assert cost.record_llm_call(object(), [1], {"a": 1}, -5, float("nan"), journal_path=path) in (
        True,
        False,
    )


def test_journal_skips_corrupt_lines(tmp_path):
    path = tmp_path / "j.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("ini bukan json\n")
        fh.write(json.dumps(_row()) + "\n")
        fh.write("[1,2,3]\n")  # JSON valid tapi bukan dict -> dilewati
        fh.write("\n")
    rows = list(cost.iter_journal(path))
    assert len(rows) == 1


def test_iter_journal_missing_file(tmp_path):
    assert list(cost.iter_journal(tmp_path / "tidak-ada.jsonl")) == []


# ------------------------------------------------------------------- summary


def _aged(days_ago: float, **kw):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return _row(timestamp=ts, **kw)


def test_summary_day_window(tmp_path):
    path = tmp_path / "j.jsonl"
    _write_rows(
        path,
        [
            _aged(0.1, input_tokens=1000, output_tokens=500),  # masuk day
            _aged(2.0, input_tokens=1000, output_tokens=500),  # di luar day
        ],
    )
    s = cost.cost_summary("day", journal_path=path)
    assert s["calls"] == 1
    assert s["total_input_tokens"] == 1000
    assert s["total_usd"] == pytest.approx(0.0075)


def test_summary_week_and_month_windows(tmp_path):
    path = tmp_path / "j.jsonl"
    _write_rows(
        path,
        [
            _aged(3.0, input_tokens=1000, output_tokens=0),
            _aged(20.0, input_tokens=1000, output_tokens=0),
        ],
    )
    assert cost.cost_summary("week", journal_path=path)["calls"] == 1
    assert cost.cost_summary("month", journal_path=path)["calls"] == 2


def test_summary_by_tool_and_by_day(tmp_path):
    path = tmp_path / "j.jsonl"
    _write_rows(
        path,
        [
            _aged(0.05, kind="tool", tool_name="web_search", input_tokens=2000, output_tokens=0),
            _aged(0.05, kind="tool", tool_name="web_search", input_tokens=1000, output_tokens=0),
            _aged(0.05, input_tokens=1000, output_tokens=1000),
        ],
    )
    s = cost.cost_summary("day", journal_path=path)
    assert s["calls"] == 3
    by_tool = {t["tool"]: t for t in s["by_tool"]}
    assert by_tool["web_search"]["calls"] == 2
    assert by_tool["__llm__"]["calls"] == 1
    # termahal dulu: llm (2.5k*2.5+1k*10) > web_search (3k*2.5)
    assert s["by_tool"][0]["tool"] == "__llm__"
    assert len(s["by_day"]) == 1
    assert s["by_day"][0]["calls"] == 3


def test_summary_identity_filter(tmp_path):
    path = tmp_path / "j.jsonl"
    _write_rows(
        path,
        [
            _aged(0.05, identity="alice"),
            _aged(0.05, identity="bob"),
        ],
    )
    s = cost.cost_summary("day", identity="alice", journal_path=path)
    assert s["calls"] == 1
    assert s["identity"] == "alice"


def test_summary_unpriced_counted(tmp_path):
    path = tmp_path / "j.jsonl"
    _write_rows(path, [_aged(0.05, model="misterius-9000", input_tokens=100, output_tokens=50)])
    s = cost.cost_summary("day", journal_path=path)
    assert s["unpriced_calls"] == 1
    assert s["total_usd"] == 0.0


def test_summary_bad_period_raises_cost_error():
    with pytest.raises(CostError):
        cost.cost_summary("year")


def test_summary_empty_journal(tmp_path):
    s = cost.cost_summary("day", journal_path=tmp_path / "kosong.jsonl")
    assert s["calls"] == 0
    assert s["total_usd"] == 0.0
    assert s["by_tool"] == []
    assert s["by_day"] == []


# ------------------------------------------------------------- handler & defs


def test_cost_report_tool_ok(tmp_path):
    path = tmp_path / "j.jsonl"
    cost.record_llm_call("u", "gpt-4o", 1000, 500, 10.0, journal_path=path)
    # Handler membaca dari journal default (override fixture), jadi tulis
    # ulang ke sana agar deterministik.
    cost.record_llm_call("u", "gpt-4o", 1000, 500, 10.0)
    out = cost.cost_report_tool("day")
    assert "ERROR" not in out
    assert "Biaya Zeline" in out
    assert "$" in out


def test_cost_report_tool_bad_period_no_raw_raise():
    out = cost.cost_report_tool("millennium")
    assert out.startswith("ERROR:")


def test_cost_today_tool_alias():
    cost.record_tool_call("u", "read_file", "gpt-4o-mini", 5.0)
    out = cost.cost_today_tool()
    assert "ERROR" not in out
    assert "read_file" in out


def test_tool_handlers_map():
    assert set(cost.TOOL_HANDLERS) == {"cost_report", "cost_today"}
    assert cost.TOOL_HANDLERS["cost_report"] is cost.cost_report_tool
    assert cost.TOOL_HANDLERS["cost_today"] is cost.cost_today_tool


def test_get_tool_defs_lazy_no_circular():
    defs = cost.get_tool_defs()
    assert {d.name for d in defs} == {"cost_report", "cost_today"}
    for d in defs:
        assert d.risk == "read"
        assert d.schema()["type"] == "function"
    names = {d.name for d in defs}
    assert "cost_report" in names
    report = next(d for d in defs if d.name == "cost_report")
    assert report.parameters["properties"]["period"]["enum"] == ["day", "week", "month"]


def test_register_cli_parses():
    import argparse

    parser = argparse.ArgumentParser(prog="zeline")
    sub = parser.add_subparsers(dest="command")
    cost.register_cli(sub)
    ns = parser.parse_args(["cost", "report", "--period", "week"])
    assert ns.cost_action == "report"
    assert ns.period == "week"
    ns2 = parser.parse_args(["cost", "today"])
    assert ns2.cost_action == "today"
    ns3 = parser.parse_args(["cost", "report"])
    assert ns3.period == "day"


def test_cmd_cost_report_and_today(capsys):
    assert cost.cmd_cost_report("day") == 0
    assert "Biaya Zeline" in capsys.readouterr().out
    assert cost.cmd_cost_today() == 0
    assert "Biaya Zeline" in capsys.readouterr().out


def test_cmd_cost_report_bad_period(capsys):
    assert cost.cmd_cost_report("abad") == 2
    assert "ERROR" in capsys.readouterr().out


# -------------------------------------------------------------------- privasi


def test_no_prompt_content_in_journal(tmp_path):
    path = tmp_path / "j.jsonl"
    secret = "sk-super-rahasia-12345"
    cost.record_llm_call(secret, "gpt-4o", 10, 10, 1.0, journal_path=path)
    cost.record_tool_call("u", "tool-dengan\x00kontrol", "gpt-4o", 1.0, journal_path=path)
    raw = path.read_text(encoding="utf-8")
    assert "sk-super-rahasia-12345" in raw  # identity memang disimpan (metadata)
    assert "\x00" not in raw  # karakter kontrol dibersihkan


def test_journal_path_always_under_data_dir(monkeypatch):
    monkeypatch.setattr(cost, "_JOURNAL_OVERRIDE", None)
    monkeypatch.setattr(cost.config, "DATA_DIR", Path("/tmp/xyz-data"), raising=False)
    p = cost.journal_path()
    assert p == Path("/tmp/xyz-data") / cost.JOURNAL_NAME
    assert ".." not in str(p)
