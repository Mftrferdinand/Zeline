"""
tools/eval.py — Agentic Eval, Self-Critique & Variance Testing  (v4.1)

Ngukur output sebelum operator yang ngukur. Tiga hal:

1. Eval        — kumpulan Case (input → assert), jalankan, lapor pass_rate + kegagalan.
2. variance()  — jalanin task N kali, ukur konsistensi (LLM/agent non-deterministik).
3. critique()  — checklist self-critique adversarial (refute sebelum kirim).

Read/measure only. Gak nyentuh dana, gak edit skill. Hasil → sk57 (debug) / sk55 (lesson).
Keyless, stdlib saja.

Pakai:
    from eval import Eval, Case, variance, critique
    ev = Eval("mint-detector")
    ev.add(Case(input="...", expect=lambda r: r["fn"] == "mintPublic"))
    print(ev.run(target_fn).summary())
    print(variance(task_fn, input=payload, runs=10).report())
"""
from __future__ import annotations

import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Case:
    input: Any
    expect: Callable[[Any], bool]            # ambil hasil target_fn(input) → True/False
    name: str = ""


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalResult:
    label: str
    results: list                            # list[CaseResult]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def summary(self) -> str:
        n = len(self.results)
        ok = sum(1 for r in self.results if r.passed)
        lines = [f"📊 eval {self.label}: {ok}/{n} pass ({self.pass_rate*100:.0f}%)"]
        for r in self.results:
            if not r.passed:
                lines.append(f"   FAIL {r.name or '<case>'}: {r.detail}")
        return "\n".join(lines)


class Eval:
    def __init__(self, label: str):
        self.label = label
        self.cases: list = []

    def add(self, case: Case) -> "Eval":
        if not case.name:
            case.name = f"case{len(self.cases)+1}"
        self.cases.append(case)
        return self

    def run(self, target_fn: Callable[[Any], Any], repeat: int = 1) -> EvalResult:
        results = []
        for case in self.cases:
            passed, detail = self._run_case(case, target_fn, repeat)
            results.append(CaseResult(case.name, passed, detail))
        return EvalResult(self.label, results)

    @staticmethod
    def _run_case(case: Case, fn: Callable, repeat: int):
        for _ in range(repeat):
            try:
                out = fn(case.input)
            except Exception as e:                       # noqa: BLE001 — eval harus tangkap semua
                return False, f"raised {type(e).__name__}: {e}"
            try:
                if not case.expect(out):
                    return False, f"expect() False — actual={out!r}"
            except Exception as e:                       # noqa: BLE001
                return False, f"expect() raised: {e}"
        return True, ""


# ───────────────── variance testing ─────────────────
@dataclass
class VarianceResult:
    runs: int
    modes: Counter
    errors: int

    @property
    def consistency(self) -> float:
        """Fraksi run yang masuk mode paling sering (1.0 = deterministik)."""
        if not self.modes:
            return 0.0
        return self.modes.most_common(1)[0][1] / self.runs

    def verdict(self) -> str:
        c = self.consistency
        if c >= 0.95:
            return "STABLE — boleh otomasi/auto_confirm"
        if c >= 0.70:
            return "FLAKY — butuh konfirmasi per-run / perbaiki dulu"
        return "UNSTABLE — JANGAN otomasi, cari sumber variance (sk57)"

    def report(self) -> str:
        modes = ", ".join(f"{k}:{v}" for k, v in self.modes.most_common())
        return (f"🎲 variance {self.runs} runs | consistency {self.consistency:.2f} "
                f"| {self.verdict()}\n   modes: {{{modes}}} | errors: {self.errors}")


def variance(task_fn: Callable[..., Any], input: Any = None, runs: int = 10,
             classify: Optional[Callable[[Any], str]] = None) -> VarianceResult:
    """Jalanin task_fn(input) `runs` kali, kelompokin hasil jadi 'mode'.

    classify(out) → label string buat ngegrup hasil. Default: repr() dipangkas.
    """
    if classify is None:
        classify = lambda out: ("ok:" + repr(out))[:60]
    modes: Counter = Counter()
    errors = 0
    for _ in range(runs):
        try:
            out = task_fn(input) if input is not None else task_fn()
            modes[classify(out)] += 1
        except Exception as e:                           # noqa: BLE001
            errors += 1
            modes[f"error:{type(e).__name__}"] += 1
    return VarianceResult(runs, modes, errors)


# ───────────────── self-critique checklist ─────────────────
CRITIQUE_PROMPTS = [
    "Gimana output ini bisa SALAH secara faktual/logis? (default: ada yang salah)",
    "Klaim mana yang gak ada bukti / belum diuji?",
    "Edge case apa yang belum di-handle? (kosong, null, batas, race, timeout)",
    "Asumsi tak tertulis apa yang kalau salah, semuanya runtuh?",
    "Kalau ini nyentuh dana/prod: apa skenario terburuk & udah di-gate?",
]


def critique() -> str:
    """Return checklist refute. Jawab tiap poin SEBELUM kirim output berisiko."""
    return "🔎 SELF-CRITIQUE (refute dulu):\n" + "\n".join(f"  {i+1}. {q}" for i, q in enumerate(CRITIQUE_PROMPTS))


# ───────────────── LLM-as-judge (production-grade) ─────────────────
def _judge_prompt(output: str, rubric: list, scale: int) -> str:
    crit = "\n".join(f"- {c}" for c in rubric)
    return (
        "Kamu juri yang ketat & skeptis. Nilai OUTPUT terhadap tiap KRITERIA.\n"
        f"Skala 1-{scale} (1=gagal total, {scale}=sempurna). Default skeptis kalau ragu.\n\n"
        f"KRITERIA:\n{crit}\n\nOUTPUT:\n\"\"\"\n{output}\n\"\"\"\n\n"
        "Balas JSON: {\"scores\": {kriteria: skor}, \"reasons\": [..], \"pass\": bool}. "
        f"pass=true hanya kalau semua skor >= {max(2, scale - 1)}."
    )


def llm_judge(output: str, rubric: list, scale: int = 5, call_fn=None, threshold=None) -> dict:
    """LLM-as-judge. Nilai `output` vs `rubric` (list kriteria) pakai LLM lain.

    call_fn(prompt)->str: penyedia LLM. Kalau None, coba model_registry (R7 cascade).
    Juri HARUS beda dari pembuat output (hindari bias). Untuk taruhan tinggi → panggil
    beberapa kali + mayoritas (panel). Juri sendiri sebaiknya di-`variance()`-cek.
    """
    if threshold is None:
        threshold = max(2, scale - 1)
    if call_fn is None:
        try:
            from model_registry import ModelRegistry            # type: ignore
            reg = ModelRegistry()
            call_fn = lambda p: reg.call_with_cascade([{"role": "user", "content": p}])
        except Exception as e:                                   # noqa: BLE001
            return {"error": f"no LLM provider — {e}", "hint": "kasih call_fn atau `add model` (sk7)",
                    "pass": None, "scores": {}, "reasons": []}
    import json as _json
    try:
        raw = call_fn(_judge_prompt(output, rubric, scale))
        start, end = raw.find("{"), raw.rfind("}")
        data = _json.loads(raw[start:end + 1]) if start >= 0 else {"raw": raw}
    except Exception as e:                                       # noqa: BLE001
        return {"error": f"judge gagal parse: {e}", "pass": None, "scores": {}, "reasons": []}
    scores = data.get("scores", {})
    if "pass" not in data and scores:
        data["pass"] = all(v >= threshold for v in scores.values())
    return data


# ───────────────── regression suite (golden-set, v4.2) ─────────────────
import json as _json2
from pathlib import Path as _Path


@dataclass
class RegressionCase:
    name: str
    input: Any


@dataclass
class RegressionResult:
    label: str
    passed: list = field(default_factory=list)      # nama case yang sama dengan golden
    regressed: list = field(default_factory=list)    # [(name, golden, actual)]
    new_cases: list = field(default_factory=list)     # belum ada di golden
    errored: list = field(default_factory=list)       # [(name, error)]

    @property
    def ok(self) -> bool:
        return not self.regressed and not self.errored

    def summary(self) -> str:
        head = (f"🧷 regression {self.label}: "
                f"{'PASS' if self.ok else 'FAIL'} | "
                f"{len(self.passed)} stable, {len(self.regressed)} regressed, "
                f"{len(self.errored)} error, {len(self.new_cases)} new")
        lines = [head]
        for name, golden, actual in self.regressed:
            lines.append(f"   ✗ {name}: golden={golden!r} → actual={actual!r}")
        for name, err in self.errored:
            lines.append(f"   ⚠ {name}: {err}")
        for name in self.new_cases:
            lines.append(f"   + {name}: baru — jalankan record_baseline buat kunci")
        return "\n".join(lines)


class RegressionSuite:
    """Golden-set regression: rekam output yang udah-benar sekali, lalu tiap rilis
    bandingin output baru vs golden. Jadi tiap naik versi kamu TAU kalau ada kemampuan
    yang rusak/melemah, bukan baru ketahuan pas operator ketemu bug.

    Pakai:
        s = RegressionSuite("mint-detector", "golden/mint.json")
        s.add("opensea-721", "opensea.io/.../1")
        s.record_baseline(detect_fn)        # sekali, pas output udah bener
        # ... rilis berikutnya ...
        print(s.run(detect_fn).summary())   # FAIL kalau ada yang berubah
    """

    def __init__(self, label: str, store: str | _Path,
                 normalize: Optional[Callable[[Any], Any]] = None):
        self.label = label
        self.store = _Path(store)
        self.cases: list = []
        self.golden: dict = {}
        # normalize: bikin output bisa dibandingin stabil (mis. buang timestamp)
        self.normalize = normalize or (lambda x: x)
        if self.store.exists():
            self.golden = _json2.loads(self.store.read_text())

    def add(self, name: str, input: Any) -> "RegressionSuite":
        self.cases.append(RegressionCase(name, input))
        return self

    def _norm(self, out: Any) -> Any:
        return self.normalize(out)

    def record_baseline(self, target_fn: Callable[[Any], Any]) -> dict:
        """Jalankan semua case, simpan output ter-normalisasi sebagai golden."""
        golden = {}
        for c in self.cases:
            golden[c.name] = self._norm(target_fn(c.input))
        self.golden = golden
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(_json2.dumps(golden, indent=2, default=str, sort_keys=True))
        return golden

    def run(self, target_fn: Callable[[Any], Any]) -> RegressionResult:
        res = RegressionResult(self.label)
        for c in self.cases:
            try:
                actual = self._norm(target_fn(c.input))
            except Exception as e:                       # noqa: BLE001
                res.errored.append((c.name, f"{type(e).__name__}: {e}"))
                continue
            if c.name not in self.golden:
                res.new_cases.append(c.name)
                continue
            # bandingin via JSON-normalisasi biar urutan dict gak ganggu
            g = _json2.dumps(self.golden[c.name], sort_keys=True, default=str)
            a = _json2.dumps(actual, sort_keys=True, default=str)
            if g == a:
                res.passed.append(c.name)
            else:
                res.regressed.append((c.name, self.golden[c.name], actual))
        return res


if __name__ == "__main__":
    # smoke test
    ev = Eval("demo").add(Case(2, lambda r: r == 4)).add(Case(3, lambda r: r == 9))
    print(ev.run(lambda x: x * x).summary())
    print(variance(lambda: 1, runs=5).report())
    print(critique())
