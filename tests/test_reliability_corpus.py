"""Actual subprocess crashes with disposable configuration and benign effects."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

CASES = ["archive-punctuation", "archive-long", "archive-legacy", "store-isolation", "compact-twice",
         "job-before-effect", "job-after-effect", "job-after-commit",
         "turn-before-effect", "turn-after-effect", "turn-after-checkpoint",
         "reader-own", "reader-foreign", "reader-legacy", "reader-symlink", "reader-full", "reader-shared-root", "reader-search-foreign", "reader-search-own", "reader-search-full", "reader-search-legacy", "reader-search-ambiguous"]


@pytest.mark.parametrize("case", CASES)
def test_reliability_contract(tmp_path, case):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ZELINE_", "OPENAI_", "ANTHROPIC_", "OPENROUTER_", "TELEGRAM_"))
           and not key.endswith(("_API_KEY", "_TOKEN"))}
    env.update(HOME=str(tmp_path / "home"), ZELINE_HOME=str(tmp_path / "data"),
               PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, str(Path(__file__).with_name("reliability_probe.py")), "--probe", case, str(workspace)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=90, env=env)
    assert process.returncode == 0, process.stdout + process.stderr
    record = json.loads(process.stdout.strip().splitlines()[-1])
    assert record["data_kind"] == "synthetic"
    assert record["provenance"]["data_kind"] == "synthetic"
    assert record["provenance"]["runtime_sha"] is None
    assert record["provenance"]["workload_id"] == case
    report = os.environ.get("WAVE1_REPORT_DIR")
    if report:
        root = Path(report)
        root.mkdir(parents=True, exist_ok=True)
        (root / (case + ".json")).write_text(json.dumps(record, indent=2) + "\n")

    if record["status"] == "skipped":
        pytest.skip(record["evidence"].get("reason", "fixture capability unavailable"))
    assert record["status"] == "passed"
