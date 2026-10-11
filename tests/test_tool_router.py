"""Tests for zeline.tool_router — fast keyword pre-filter over native tools."""

from __future__ import annotations

import os
import time

import pytest

from zeline import tool_router
from zeline.tool_router import (
    ESSENTIAL_TOOLS,
    ROUTER_ENABLED,
    ToolRouterError,
    configure,
    filter_schemas,
    route,
    route_names,
)
from zeline.tools import TOOL_DEFS


@pytest.fixture(autouse=True)
def _reset_router_state():
    """Restore default router config after every test."""
    yield
    tool_router.ROUTER_ENABLED = True
    configure(enabled=True, top_k=15)
    os.environ.pop("ZELINE_ROUTER_OFF", None)


def _names_descs():
    return [(t.name, t.description) for t in TOOL_DEFS]


def _schemas():
    return [t.schema() for t in TOOL_DEFS]


# ---------------------------------------------------------------------------
# (a) 20 real-world queries (EN + ID mix) — the right tool must be top-15
# ---------------------------------------------------------------------------

QUERIES = [
    ("join zoom meeting", "meeting_join"),
    ("buatkan kartu kanban baru", "kanban_create"),
    ("kirim email ke budi@example.com", "email_send"),
    ("berapa biaya kemarin", "runtime_info"),
    ("cari di web tentang harga emas hari ini", "web_search"),
    ("set pengingat besok pagi", "schedule_task"),
    ("baca email masuk terbaru", "gmail_read"),
    ("buat issue baru di github", "github_create_issue"),
    ("bagaimana cuaca hari ini", "openweathermap_current_weather"),
    ("tulis file catatan baru", "write_file"),
    ("jalankan perintah shell", "run_shell"),
    ("cari file yang berisi kata rahasia", "search_files"),
    ("tambah tugas ke todoist", "todoist_add_task"),
    ("kirim pesan slack ke tim", "slack_send_message"),
    ("kirim sms ke pelanggan", "twilio_send_sms"),
    ("posting tweet baru", "x_api_post_tweet"),
    ("tambah goal baru", "goal_add"),
    ("transcribe audio rekaman", "transcribe_audio"),
    ("ingatkan saya minum obat tiap jam", "schedule_task"),
    ("download file dari url", "download_file"),
]


@pytest.mark.parametrize("query,want", QUERIES)
def test_query_routes_to_expected_tool(query, want):
    top15 = route_names(query, _names_descs())[:15]
    assert want in top15, f"{query!r}: {want} not in top-15: {top15}"


@pytest.mark.parametrize("query,want", QUERIES)
def test_route_returns_tooldef_objects(query, want):
    got = route(query, TOOL_DEFS)
    assert len(got) == 15
    names = [t.name for t in got]
    assert want in names
    assert all(hasattr(t, "name") and hasattr(t, "description") for t in got)


def test_route_accepts_plain_dicts():
    tools = [
        {"name": "web_search", "description": "Search the web"},
        {"name": "run_shell", "description": "Run a shell command"},
    ]
    got = route("cari di web", tools)
    assert [t["name"] for t in got] == ["web_search", "run_shell"]


# ---------------------------------------------------------------------------
# (b) timing: route() over all tools < 50ms (max of 5 runs)
# ---------------------------------------------------------------------------


def test_route_timing_under_50ms():
    route("warmup query", TOOL_DEFS)  # warm caches / allocator
    worst = 0.0
    for _ in range(5):
        start = time.perf_counter()
        route("kirim email dan cari di web tentang emas", TOOL_DEFS)
        worst = max(worst, time.perf_counter() - start)
    assert worst < 0.050, f"route() took {worst * 1000:.1f}ms (budget 50ms)"


# ---------------------------------------------------------------------------
# (c) ask_user is always included
# ---------------------------------------------------------------------------


def test_ask_user_always_included():
    for q in ["", "zxqwkj asdfgh", "kirim email", " ".join(["x"] * 200)]:
        names = [t.name for t in route(q, TOOL_DEFS)]
        assert "ask_user" in names, f"ask_user missing for query {q!r}"
        assert names[0] == "ask_user"  # essentials go first


def test_essentials_constant():
    assert ESSENTIAL_TOOLS == frozenset({"ask_user"})
    assert "ask_user" in {t.name for t in TOOL_DEFS}


# ---------------------------------------------------------------------------
# (d) fail-open: filter_schemas with broken input returns the input as-is
# ---------------------------------------------------------------------------


def test_filter_schemas_fail_open_on_garbage():
    garbage = [None, "junk", 123, {"nope": True}]
    assert filter_schemas("kirim email", garbage) == garbage


def test_filter_schemas_fail_open_on_broken_function_block():
    broken = [{"type": "function", "function": {"no_name": 1}}]
    assert filter_schemas("kirim email", broken) == broken


def test_filter_schemas_fail_open_on_non_sequence():
    assert filter_schemas("kirim email", None) == []  # type: ignore[arg-type]


def test_filter_schemas_happy_path():
    got = filter_schemas("join zoom meeting", _schemas())
    assert len(got) == 15
    names = [s["function"]["name"] for s in got]
    assert "meeting_join" in names
    assert names[0] == "ask_user"
    # OpenAI schema shape preserved
    assert got[0]["type"] == "function"
    assert "parameters" in got[0]["function"]


def test_filter_schemas_empty_query_returns_essentials_plus_generic():
    got = filter_schemas("", _schemas())
    names = [s["function"]["name"] for s in got]
    assert names[0] == "ask_user"
    assert "web_search" in names


def test_route_raises_tool_router_error_on_bad_entry():
    with pytest.raises(ToolRouterError):
        route("hello", [{"oops": 1}])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# (e) configure off -> route returns everything
# ---------------------------------------------------------------------------


def test_configure_off_returns_all_tools():
    configure(enabled=False)
    got = route("kirim email", TOOL_DEFS)
    assert len(got) == len(TOOL_DEFS)
    assert got == list(TOOL_DEFS)


def test_env_opt_out_returns_all_tools():
    os.environ["ZELINE_ROUTER_OFF"] = "1"
    got = route("kirim email", TOOL_DEFS)
    assert len(got) == len(TOOL_DEFS)
    filtered = filter_schemas("kirim email", _schemas())
    assert len(filtered) == len(TOOL_DEFS)


def test_configure_top_k_clamped():
    configure(top_k=3)
    assert len(route("kirim email", TOOL_DEFS)) == 5  # clamped to MIN 5
    configure(top_k=999)
    assert len(route("kirim email", TOOL_DEFS)) == 50  # clamped to MAX 50
    configure(top_k=10)
    assert len(route("kirim email", TOOL_DEFS)) == 10


def test_router_enabled_flag_exposed():
    assert ROUTER_ENABLED is True
    configure(enabled=False)
    assert tool_router.ROUTER_ENABLED is False


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


def test_empty_tools_returns_empty():
    assert route("kirim email", []) == []


def test_empty_query_returns_essentials_plus_generic():
    got = route("", TOOL_DEFS)
    names = [t.name for t in got]
    assert names[0] == "ask_user"
    assert "web_search" in names
    assert len(got) == 15


def test_gibberish_query_still_returns_top_k_with_essentials():
    got = route("zxqwkj vbnm asdfgh", TOOL_DEFS)
    assert len(got) == 15
    assert got[0].name == "ask_user"


def test_route_names_accepts_bare_strings():
    ranked = route_names("kirim email", ["email_send", "web_search"])
    assert ranked[0] == "email_send"


def test_route_names_rejects_malformed():
    with pytest.raises(ToolRouterError):
        route_names("x", [{"description": "no name"}])
