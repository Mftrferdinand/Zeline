"""Cost telemetry: per-call token/cost journal + report.

Jurnal append-only di ``~/.zeline/cost_journal.jsonl`` mencatat setiap panggilan
LLM dan setiap pemanggilan tool native: siapa (identity), model apa, berapa
token masuk/keluar, berapa estimasi biayanya, dan berapa lama. Dari jurnal itu
``cost_summary()`` menurunkan agregat per periode (hari/minggu/bulan) yang
dipakai tool agent ``cost_report``/``cost_today`` dan CLI ``zeline cost``.

Pemisahan tanggung jawab dengan ``zeline.usage_stats``: modul itu mencatat
*token* mentah per model per hari (SQLite) dan hanya menghitung biaya bila
operator mengisi harga sendiri. Modul ini mencatat *event per panggilan*
(append-only JSONL, termasuk tool non-LLM), menghitung biaya dari tabel harga
internal, dan fokus pada JURNAL + BIAYA + LAPORAN — bukan duplikasi accounting
token ``usage_stats``.

HARGA (aturan keras):

* ``MODEL_PRICES`` di bawah ini adalah **ESTIMASI** dari harga publik yang
  diketahui (per 1 juta token, USD). Harga provider berubah sewaktu-waktu;
  angka di sini bisa salah/stale — jangan dipakai untuk penagihan.
* Prioritas resolusi harga: ``set_model_prices()`` (runtime) >
  env ``ZELINE_PRICE_<MODEL>`` > ``config.MODEL_PRICES`` (operator, file
  config) > estimasi bawaan modul ini.
* Model yang tidak punya harga di semua sumber dicatat dengan ``cost_usd=0``
  dan dihitung sebagai ``unpriced_calls`` di ringkasan — TIDAK ditebak.
  Angka 0 yang jujur lebih baik daripada biaya yang dikarang.

PRIVASI (aturan keras, ditegakkan modul ini):

* Yang disimpan HANYA metadata: timestamp, identity, jenis panggilan, nama
  tool/model, jumlah token, biaya, durasi. Tidak pernah isi prompt, argumen
  tool, atau respons model.
* ``identity`` disimpan apa adanya (maks 200 char) supaya laporan bisa
  difilter — ini metadata sesi lokal, bukan isi percakapan.

``record_llm_call`` dan ``record_tool_call`` TIDAK PERNAH raise: seluruh
badannya dibungkus try/except. Telemetri adalah pengamat — ia tidak boleh
merusak turn agent, bahkan ketika disk penuh.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zeline import config

__all__ = [
    "CostError",
    "MODEL_PRICES",
    "JOURNAL_NAME",
    "set_model_prices",
    "reset_price_overrides",
    "price_for",
    "cost_usd",
    "record_llm_call",
    "record_tool_call",
    "cost_summary",
    "iter_journal",
    "cost_report_tool",
    "cost_today_tool",
    "TOOL_HANDLERS",
    "get_tool_defs",
    "register_cli",
    "journal_path",
]

JOURNAL_NAME = "cost_journal.jsonl"
_LLM_TOOL_NAME = "__llm__"

#: Harga ESTIMASI per 1 juta token dalam USD: nama-model -> (input, output).
#: Angka wajar dari harga publik yang diketahui; BUKAN harga kontrak operator.
#: Tandai jelas: ini estimasi, bisa stale — operator sebaiknya menimpa lewat
#: config ``agent.model_prices`` atau env ``ZELINE_PRICE_<MODEL>``.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "o3": (2.0, 8.0),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # Google
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    # xAI
    "grok-4": (3.0, 15.0),
    # Alibaba
    "qwen3-max": (1.2, 6.0),
    # Moonshot
    "kimi-k2": (0.60, 2.50),
}

#: Batas umur jendela agregasi dalam detik.
_PERIOD_SECONDS = {"day": 86400, "week": 7 * 86400, "month": 30 * 86400}


class CostError(RuntimeError):
    """Kesalahan user-facing dari modul cost telemetry."""


#: Override harga runtime dari set_model_prices(): nama -> (input, output).
_RUNTIME_PRICES: dict[str, tuple[float, float]] = {}

#: Kunci tulis jurnal: satu baris JSONL ditulis utuh di bawah kunci ini.
_WRITE_LOCK = threading.Lock()

#: Override path jurnal (dipakai test); None = default config.DATA_DIR.
_JOURNAL_OVERRIDE: Path | None = None


# ---------------------------------------------------------------- path & harga


def journal_path() -> Path:
    """Lokasi file jurnal. Selalu di bawah ``config.DATA_DIR`` — tidak pernah
    dari input user (anti path traversal)."""
    if _JOURNAL_OVERRIDE is not None:
        return _JOURNAL_OVERRIDE
    return Path(config.DATA_DIR) / JOURNAL_NAME


def _normalize_model(name: Any) -> str:
    return re.sub(r"\s+", "", str(name or "").strip().lower()) or "(unknown)"


def _is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _clean_price_pair(raw: Any) -> tuple[float, float] | None:
    """(input, output) yang valid per 1M token, atau None bila tidak valid."""
    if isinstance(raw, dict):
        raw = (raw.get("input"), raw.get("output"))
    if not isinstance(raw, (tuple, list)) or len(raw) != 2:
        return None
    if not _is_finite_number(raw[0]) or not _is_finite_number(raw[1]):
        return None
    in_p, out_p = float(raw[0]), float(raw[1])
    if in_p < 0 or out_p < 0:
        return None
    return (in_p, out_p)


def _env_prices() -> dict[str, tuple[float, float]]:
    """ZELINE_PRICE_<MODEL>=\"input,output\" — <MODEL> ditulis UPPER_SNAKE.

    Contoh: ``ZELINE_PRICE_GPT_4O="2.5,10.0"`` menimpa harga ``gpt-4o``.
    Nilai tidak valid diabaikan diam-diam (best-effort, bukan error fatal)."""
    found: dict[str, tuple[float, float]] = {}
    prefix = "ZELINE_PRICE_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        model = key[len(prefix) :].strip().lower().replace("_", "-")
        if not model:
            continue
        pair = value.split(",")
        cleaned = _clean_price_pair(pair)
        if cleaned is not None:
            found[model] = cleaned
    return found


def set_model_prices(prices: dict[str, tuple[float, float] | list[float]]) -> None:
    """Timpa harga untuk sesi ini (prioritas tertinggi). Entri tidak valid
    diabaikan. ``reset_price_overrides()`` mengembalikannya."""
    global _RUNTIME_PRICES
    cleaned: dict[str, tuple[float, float]] = {}
    for name, pair in (prices or {}).items():
        key = _normalize_model(name)
        good = _clean_price_pair(pair)
        if good is not None and key != "(unknown)":
            cleaned[key] = good
    _RUNTIME_PRICES = cleaned


def reset_price_overrides() -> None:
    """Hapus override runtime dari ``set_model_prices()``."""
    global _RUNTIME_PRICES
    _RUNTIME_PRICES = {}


def _config_prices() -> dict[str, tuple[float, float]]:
    raw = getattr(config, "MODEL_PRICES", None)
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, tuple[float, float]] = {}
    for name, pair in raw.items():
        good = _clean_price_pair(pair)
        if good is not None:
            cleaned[_normalize_model(name)] = good
    return cleaned


def _price_table() -> dict[str, tuple[float, float]]:
    """Gabung semua sumber harga sesuai prioritas (rendah -> tinggi)."""
    table = {_normalize_model(k): v for k, v in MODEL_PRICES.items()}
    table.update(_config_prices())
    table.update(_env_prices())
    table.update(_RUNTIME_PRICES)
    return table


def price_for(model: str) -> tuple[float, float] | None:
    """Harga (input, output) per 1M token untuk model, atau None bila tak
    punya harga di semua sumber. Cocokkan persis dulu, lalu prefix
    (``gpt-4o`` menutupi ``gpt-4o-2024-08-06``)."""
    name = _normalize_model(model)
    table = _price_table()
    if name in table:
        return table[name]
    # Prefix match: kunci yang merupakan awalan nama model.
    best: tuple[float, float] | None = None
    best_len = 0
    for key, pair in table.items():
        if name.startswith(key) and len(key) > best_len:
            best, best_len = pair, len(key)
    return best


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimasi biaya USD. Model tanpa harga -> 0.0 (jujur, bukan tebakan).
    Tidak pernah mengembalikan NaN/inf: token negatif dijepit ke 0."""
    in_tok = max(0, int(input_tokens or 0))
    out_tok = max(0, int(output_tokens or 0))
    pair = price_for(model)
    if pair is None:
        return 0.0
    value = (in_tok * pair[0] + out_tok * pair[1]) / 1_000_000
    return value if math.isfinite(value) else 0.0


# --------------------------------------------------------------------- record


def _sanitize_text(value: Any, limit: int = 120) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text).strip()
    return text[:limit]


def _record(
    identity: Any,
    kind: str,
    tool_name: Any,
    model: Any,
    input_tokens: Any,
    output_tokens: Any,
    duration_ms: Any,
    *,
    path: Path | None = None,
) -> bool:
    """Tulis satu baris jurnal. Mengembalikan True bila tersimpan."""
    try:
        in_tok = max(0, int(input_tokens or 0))
    except (TypeError, ValueError):
        in_tok = 0
    try:
        out_tok = max(0, int(output_tokens or 0))
    except (TypeError, ValueError):
        out_tok = 0
    try:
        dur = float(duration_ms or 0)
        dur = dur if math.isfinite(dur) and dur >= 0 else 0.0
    except (TypeError, ValueError):
        dur = 0.0
    model_name = _normalize_model(model)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "identity": _sanitize_text(identity, 200) or "cli:local",
        "kind": "llm" if kind == "llm" else "tool",
        "tool_name": _sanitize_text(tool_name) or _LLM_TOOL_NAME,
        "model": model_name,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": cost_usd(model_name, in_tok, out_tok),
        "duration_ms": dur,
    }
    line = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
    target = path if path is not None else journal_path()
    try:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return True
    except (OSError, ValueError, TypeError):
        # Disk penuh, path tidak bisa ditulis, dsb — telemetri tidak boleh
        # merusak pemanggil.
        return False


def record_llm_call(
    identity: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: float,
    *,
    journal_path: Path | None = None,
) -> bool:
    """Catat satu panggilan LLM. Best-effort: tidak pernah raise."""
    try:
        return _record(
            identity,
            "llm",
            _LLM_TOOL_NAME,
            model,
            input_tokens,
            output_tokens,
            duration_ms,
            path=journal_path,
        )
    except Exception:
        return False


def record_tool_call(
    identity: str,
    tool_name: str,
    model: str,
    duration_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    *,
    journal_path: Path | None = None,
) -> bool:
    """Catat satu pemanggilan tool. Best-effort: tidak pernah raise."""
    try:
        return _record(
            identity,
            "tool",
            tool_name,
            model,
            input_tokens,
            output_tokens,
            duration_ms,
            path=journal_path,
        )
    except Exception:
        return False


# --------------------------------------------------------------- baca & agregat


def iter_journal(path: Path | None = None) -> Any:
    """Yield baris jurnal yang valid sebagai dict, satu per satu (streaming —
    file besar tidak dimuat ke memori). Baris rusak dilewati diam-diam."""
    target = path if path is not None else journal_path()
    try:
        fh = open(target, encoding="utf-8")
    except (OSError, ValueError):
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(row, dict):
                yield row


def _parse_ts(row: dict[str, Any]) -> float | None:
    raw = row.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def cost_summary(
    period: str = "day",
    identity: str | None = None,
    *,
    journal_path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Agregat biaya dari jurnal untuk jendela ``period``.

    period: "day" (24 jam terakhir), "week" (7 hari), "month" (30 hari).
    identity: bila diisi, hanya baris dengan identity itu yang dihitung.
    Mengembalikan dict {total_usd, total_input_tokens, total_output_tokens,
    calls, unpriced_calls, by_tool, by_day, period, identity}.
    Period tidak dikenal -> CostError. Jurnal tak terbaca -> ringkasan kosong
    (degradasi, bukan raise).
    """
    if period not in _PERIOD_SECONDS:
        raise CostError(f"period tidak dikenal: {period!r} (pilih: day, week, month)")
    window = _PERIOD_SECONDS[period]
    moment = time.time() if now is None else now
    cutoff = moment - window
    ident = _sanitize_text(identity, 200) if identity else None

    total_usd = 0.0
    total_in = 0
    total_out = 0
    calls = 0
    unpriced = 0
    by_tool: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}

    for row in iter_journal(path=journal_path):
        ts = _parse_ts(row)
        if ts is None or ts < cutoff:
            continue
        if ident is not None and row.get("identity") != ident:
            continue
        try:
            in_tok = max(0, int(row.get("input_tokens") or 0))
            out_tok = max(0, int(row.get("output_tokens") or 0))
        except (TypeError, ValueError):
            continue
        tool = _sanitize_text(row.get("tool_name")) or _LLM_TOOL_NAME
        model = _normalize_model(row.get("model"))
        usd = cost_usd(model, in_tok, out_tok)
        if not math.isfinite(usd):
            usd = 0.0
        if usd == 0.0 and (in_tok or out_tok) and price_for(model) is None:
            unpriced += 1
        total_usd += usd
        total_in += in_tok
        total_out += out_tok
        calls += 1
        slot = by_tool.setdefault(tool, {"tool": tool, "usd": 0.0, "calls": 0})
        slot["usd"] = slot["usd"] + usd
        slot["calls"] += 1
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        dslot = by_day.setdefault(
            day,
            {"day": day, "usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0},
        )
        dslot["usd"] = dslot["usd"] + usd
        dslot["input_tokens"] += in_tok
        dslot["output_tokens"] += out_tok
        dslot["calls"] += 1

    tools = sorted(by_tool.values(), key=lambda s: s["usd"], reverse=True)
    days = sorted(by_day.values(), key=lambda s: s["day"])
    for slot in tools:
        slot["usd"] = round(slot["usd"], 6)
    for slot in days:
        slot["usd"] = round(slot["usd"], 6)
    return {
        "period": period,
        "identity": ident,
        "total_usd": round(total_usd, 6),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "calls": calls,
        "unpriced_calls": unpriced,
        "by_tool": tools,
        "by_day": days,
    }


# ------------------------------------------------------------- format & tools


def _fmt_usd(value: float) -> str:
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:,.3f}"
    return f"${value:,.6f}"


def _fmt_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


_PERIOD_LABEL = {"day": "24 jam terakhir", "week": "7 hari terakhir", "month": "30 hari terakhir"}


def format_summary(summary: dict[str, Any]) -> str:
    """Render ringkasan agregat menjadi teks Bahasa Indonesia."""
    period = summary.get("period", "day")
    label = _PERIOD_LABEL.get(period, period)
    ident = summary.get("identity")
    head = f"💰 Biaya Zeline — {label}"
    if ident:
        head += f" (identity: {ident})"
    lines = [
        head,
        f"Total: {_fmt_usd(summary.get('total_usd', 0.0))} "
        f"· {_fmt_tokens(summary.get('total_tokens', 0))} token "
        f"({_fmt_tokens(summary.get('total_input_tokens', 0))} in / "
        f"{_fmt_tokens(summary.get('total_output_tokens', 0))} out) "
        f"· {summary.get('calls', 0)} panggilan",
    ]
    if summary.get("unpriced_calls"):
        lines.append(
            f"⚠️ {summary['unpriced_calls']} panggilan tanpa harga "
            "(model tidak ada di tabel harga — biaya tercatat $0)."
        )
    tools = summary.get("by_tool") or []
    if tools:
        lines.append("")
        lines.append("Termahal:")
        for slot in tools[:8]:
            lines.append(f"  • {slot['tool']}: {_fmt_usd(slot['usd'])} ({slot['calls']}x)")
    days = summary.get("by_day") or []
    if len(days) > 1:
        lines.append("")
        lines.append("Per hari:")
        for slot in days[-7:]:
            lines.append(
                f"  • {slot['day']}: {_fmt_usd(slot['usd'])} "
                f"({_fmt_tokens(slot['input_tokens'] + slot['output_tokens'])} token)"
            )
    return "\n".join(lines)


def cost_report_tool(period: str = "day") -> str:
    """Handler tool agent ``cost_report``: ringkasan biaya per periode."""
    try:
        summary = cost_summary(period=period)
    except CostError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # jangan pernah propagate mentah ke agent
        return f"ERROR: gagal membaca jurnal biaya: {exc}"
    return format_summary(summary)


def cost_today_tool() -> str:
    """Handler tool agent ``cost_today``: alias cepat untuk periode harian."""
    return cost_report_tool("day")


TOOL_HANDLERS: dict[str, Any] = {
    "cost_report": cost_report_tool,
    "cost_today": cost_today_tool,
}


def get_tool_defs() -> list[Any]:
    """ToolDef untuk agent. Import ToolDef lazy di dalam fungsi agar tidak
    circular import (zeline.tools tidak mengimpor zeline.cost)."""
    from zeline.tools import ToolDef  # noqa: PLC0415  (lazy, anti-circular)

    profiles = frozenset({"safe", "workspace", "full"})
    return [
        ToolDef(
            name="cost_report",
            description=(
                "Tampilkan ringkasan biaya pemakaian Zeline (token & estimasi "
                "USD) untuk suatu periode, termasuk daftar tool/panggilan "
                "termahal. Harga bersifat ESTIMASI dari tabel publik."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["day", "week", "month"],
                        "description": "Periode laporan: day (24 jam), week (7 hari), month (30 hari).",
                    },
                },
            },
            profiles=profiles,
            risk="read",
        ),
        ToolDef(
            name="cost_today",
            description=(
                "Alias cepat: ringkasan biaya pemakaian Zeline untuk 24 jam "
                "terakhir. Sama dengan cost_report dengan period=day."
            ),
            parameters={"type": "object", "properties": {}},
            profiles=profiles,
            risk="read",
        ),
    ]


# ------------------------------------------------------------------------ CLI


def register_cli(subparsers: Any) -> None:
    """Daftarkan ``zeline cost report`` dan ``zeline cost today``.

    Koordinator memanggil ini dari ``build_parser()`` di zeline/cli.py lalu
    me-routing ``args.cost_action`` ke handler yang sesuai.
    """
    cost_p = subparsers.add_parser("cost", help="laporan biaya pemakaian (token & estimasi USD)")
    cost_sub = cost_p.add_subparsers(dest="cost_action")

    rep = cost_sub.add_parser("report", help="ringkasan biaya per periode")
    rep.add_argument(
        "--period",
        choices=["day", "week", "month"],
        default="day",
        help="periode laporan (default: day)",
    )
    rep.set_defaults(cost_action="report")

    today = cost_sub.add_parser("today", help="ringkasan biaya 24 jam terakhir")
    today.set_defaults(cost_action="today")


def cmd_cost_report(period: str = "day") -> int:
    """Entry CLI ``zeline cost report``. Return 0 sukses, 2 input salah."""
    try:
        out = cost_report_tool(period)
    except CostError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(out)
    return 2 if out.startswith("ERROR:") else 0


def cmd_cost_today() -> int:
    """Entry CLI ``zeline cost today``."""
    print(cost_today_tool())
    return 0
