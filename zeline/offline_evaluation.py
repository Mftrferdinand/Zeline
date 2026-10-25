"""Bounded offline task evaluation for reviewable SkillForge candidates.

This module is evaluation infrastructure, not an optimizer and not evidence of
model-wide generalization. Adapter code is operator-trusted and digest-pinned;
candidate skill text and task inputs are passed to it strictly as data.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

MAX_CASES_PER_SPLIT = 16
MAX_MANIFEST_BYTES = 256 * 1024
MAX_CANDIDATE_BYTES = 256 * 1024
MAX_ADAPTER_BYTES = 128 * 1024
MAX_RESULT_BYTES = 64 * 1024
DEFAULT_TASK_TIMEOUT = 5.0
DEFAULT_TOTAL_TIMEOUT = 60.0

_WORKER = r'''
import contextlib, importlib.util, json, os, sys
request = json.loads(sys.stdin.read())
spec = importlib.util.spec_from_file_location("zeline_trusted_adapter", request["adapter"])
if spec is None or spec.loader is None:
    raise RuntimeError("adapter cannot be loaded")
module = importlib.util.module_from_spec(spec)
with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
    spec.loader.exec_module(module)
    result = module.run(request["candidate"], request["input"])
sys.stdout.write(json.dumps({"result": result}, sort_keys=True, separators=(",", ":")))
'''


def _canonical(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("Evaluation manifest values must be finite JSON data") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Evaluation manifest exceeds the byte limit")
    return raw


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON values without Python's bool/int or int/float coercions."""
    return _canonical(actual) == _canonical(expected)


def _terminate_worker(proc: subprocess.Popen) -> None:
    """Kill the isolated worker process group and reap its direct process."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, FileNotFoundError, subprocess.SubprocessError):
        pass
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class _BoundedCapture:
    """Drain a worker pipe while retaining at most limit+1 bytes."""

    def __init__(self, stream, limit: int, name: str) -> None:
        self.stream = stream
        self.limit = limit
        self.name = name
        self.data = bytearray()
        self.overflow = False
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True, name=name)

    def start(self) -> None:
        self.thread.start()

    def _read(self) -> None:
        try:
            while len(self.data) <= self.limit:
                chunk = self.stream.read(min(8192, self.limit + 1 - len(self.data)))
                if not chunk:
                    break
                self.data.extend(chunk)
                if len(self.data) > self.limit:
                    self.overflow = True
                    break
        except (OSError, ValueError):
            # The parent may close the pipe while retiring a timed-out worker.
            pass
        finally:
            self.done.set()

    def join(self, timeout: float) -> None:
        self.thread.join(timeout=timeout)

    def close(self) -> None:
        try:
            self.stream.close()
        except (OSError, ValueError):
            pass



def _validate_digest(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(label + " must be a 64-character SHA256 digest")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(label + " must be hexadecimal") from exc
    return value.lower()


def _validate_manifest(manifest: Any) -> tuple[dict[str, list[dict[str, Any]]], bytes]:
    if not isinstance(manifest, dict) or set(manifest) != {"training", "held_out"}:
        raise ValueError("Manifest must contain only training and held_out partitions")
    frozen: dict[str, list[dict[str, Any]]] = {}
    all_ids: set[str] = set()
    inputs: dict[str, set[str]] = {"training": set(), "held_out": set()}
    for partition in ("training", "held_out"):
        cases = manifest[partition]
        if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES_PER_SPLIT:
            raise ValueError("Each partition must contain 1 to 16 cases")
        validated = []
        for case in cases:
            if (not isinstance(case, dict)
                    or set(case) not in ({"id", "input", "expected"},
                                         {"id", "input", "expected", "input_sha256"})):
                raise ValueError("Each case requires exactly id, input and expected")
            case_id = case["id"]
            if (not isinstance(case_id, str) or not 1 <= len(case_id) <= 80
                    or any(not (c.isascii() and (c.isalnum() or c in "_.:-")) for c in case_id)):
                raise ValueError("Case IDs must be bounded stable ASCII identifiers")
            if case_id in all_ids:
                raise ValueError("Training and held-out case IDs must be unique")
            all_ids.add(case_id)
            input_digest = _sha(_canonical(case["input"]))
            if "input_sha256" in case and case["input_sha256"] != input_digest:
                raise ValueError("Frozen task input digest mismatch")
            inputs[partition].add(input_digest)
            validated.append({"id": case_id, "input": case["input"],
                              "expected": case["expected"], "input_sha256": input_digest})
        frozen[partition] = validated
    overlap = inputs["training"] & inputs["held_out"]
    if overlap:
        raise ValueError("Training and held-out inputs must be disjoint, not only their IDs")
    raw = _canonical(frozen)
    return frozen, raw


def freeze_evaluation(store, proposal_id: str, manifest: Any, *, adapter_path,
                      adapter_sha256: str, task_timeout: float = DEFAULT_TASK_TIMEOUT,
                      total_timeout: float = DEFAULT_TOTAL_TIMEOUT) -> dict[str, Any]:
    """Freeze a proposal, dataset and trusted adapter into a replayable receipt."""
    if (isinstance(task_timeout, bool) or not isinstance(task_timeout, (int, float))
            or not 0.05 <= task_timeout <= 30.0):
        raise ValueError("task_timeout must be between 0.05 and 30 seconds")
    if (isinstance(total_timeout, bool) or not isinstance(total_timeout, (int, float))
            or not task_timeout <= total_timeout <= 300.0):
        raise ValueError("total_timeout must cover a task and stay within 300 seconds")
    proposal = store.get(proposal_id)
    store._unchanged(proposal)
    frozen_manifest, manifest_raw = _validate_manifest(manifest)
    path = Path(adapter_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix != ".py":
        raise ValueError("Trusted adapter must be a regular Python source file")
    adapter = path.read_bytes()
    if len(adapter) > MAX_ADAPTER_BYTES:
        raise ValueError("Trusted adapter exceeds the byte limit")
    expected_adapter = _validate_digest(adapter_sha256, "adapter_sha256")
    if _sha(adapter) != expected_adapter:
        raise ValueError("Trusted adapter digest mismatch")
    if len(proposal["content"].encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise ValueError("Candidate exceeds the offline evaluation byte limit")
    checker_sha256 = _sha(("host-json-equality-v1\n" + _sha(_WORKER.encode())).encode())
    return {
        "schema_version": 1,
        "scope": "evaluation_readiness",
        "proposal_id": proposal_id,
        "proposal_candidate": proposal["candidate"],
        "proposal_base": proposal["base"],
        "manifest": frozen_manifest,
        "manifest_sha256": _sha(manifest_raw),
        "adapter_path": str(path),
        "adapter_sha256": expected_adapter,
        "checker_sha256": checker_sha256,
        "task_timeout": float(task_timeout),
        "total_timeout": float(total_timeout),
        "max_case_evaluations": 2 * sum(len(v) for v in frozen_manifest.values()),
    }


def _run_case(adapter: Path, candidate: str, task_input: Any, timeout: float) -> Any:
    request = _canonical({"adapter": str(adapter), "candidate": candidate, "input": task_input})
    if len(request) > MAX_CANDIDATE_BYTES + MAX_MANIFEST_BYTES:
        raise ValueError("Offline worker request exceeds the byte limit")
    with tempfile.TemporaryFile() as request_file:
        request_file.write(request)
        request_file.seek(0)
        options = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        proc = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", _WORKER], stdin=request_file,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options,
        )
        assert proc.stdout is not None and proc.stderr is not None
        stdout_capture = _BoundedCapture(proc.stdout, MAX_RESULT_BYTES, "zeline-eval-stdout")
        stderr_capture = _BoundedCapture(proc.stderr, MAX_RESULT_BYTES, "zeline-eval-stderr")
        stdout_capture.start()
        stderr_capture.start()
        deadline = time.monotonic() + timeout
        failure = None
        try:
            while proc.poll() is None:
                if stdout_capture.overflow or stderr_capture.overflow:
                    failure = ValueError("Offline adapter output exceeds the byte limit")
                    break
                if time.monotonic() >= deadline:
                    failure = TimeoutError("Offline adapter task timed out")
                    break
                time.sleep(0.005)
        finally:
            # Always terminate the dedicated group: an adapter may have spawned
            # descendants even when its direct worker already exited normally.
            _terminate_worker(proc)
            stdout_capture.join(timeout=1)
            stderr_capture.join(timeout=1)
            stdout_capture.close()
            stderr_capture.close()
        if stdout_capture.overflow or stderr_capture.overflow:
            failure = ValueError("Offline adapter output exceeds the byte limit")
        if failure is not None:
            raise failure
        stdout = bytes(stdout_capture.data)
        if proc.returncode != 0:
            raise ValueError("Offline adapter task failed")
    try:
        payload = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Offline adapter returned malformed JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"result"}:
        raise ValueError("Offline adapter returned an invalid result envelope")
    _canonical(payload["result"])
    return payload["result"]


def evaluate(store, frozen: dict[str, Any]) -> dict[str, Any]:
    """Execute paired tasks and publish evidence through SkillForge's CAS gate."""
    required = {"schema_version", "scope", "proposal_id", "proposal_candidate",
                "proposal_base", "manifest", "manifest_sha256", "adapter_path",
                "adapter_sha256", "checker_sha256", "task_timeout", "total_timeout",
                "max_case_evaluations"}
    if not isinstance(frozen, dict) or set(frozen) != required or frozen.get("schema_version") != 1:
        raise ValueError("Invalid frozen evaluation receipt")
    if frozen["scope"] != "evaluation_readiness":
        raise ValueError("Invalid evaluation scope")
    manifest, manifest_raw = _validate_manifest(frozen["manifest"])
    if _sha(manifest_raw) != _validate_digest(frozen["manifest_sha256"], "manifest_sha256"):
        raise ValueError("Frozen manifest digest mismatch")
    checker = _sha(("host-json-equality-v1\n" + _sha(_WORKER.encode())).encode())
    if checker != _validate_digest(frozen["checker_sha256"], "checker_sha256"):
        raise ValueError("Host checker digest mismatch")
    proposal = store.get(frozen["proposal_id"])
    store._unchanged(proposal)
    if (proposal["candidate"] != frozen["proposal_candidate"]
            or proposal["base"] != frozen["proposal_base"]):
        raise ValueError("Proposal moved after evaluation was frozen")
    adapter = Path(frozen["adapter_path"]).resolve(strict=True)
    adapter_bytes = adapter.read_bytes()
    if len(adapter_bytes) > MAX_ADAPTER_BYTES or _sha(adapter_bytes) != _validate_digest(
            frozen["adapter_sha256"], "adapter_sha256"):
        raise ValueError("Trusted adapter changed after evaluation was frozen")
    task_timeout = frozen["task_timeout"]
    total_timeout = frozen["total_timeout"]
    if (isinstance(task_timeout, bool) or not isinstance(task_timeout, (int, float))
            or not 0.05 <= task_timeout <= 30.0
            or isinstance(total_timeout, bool) or not isinstance(total_timeout, (int, float))
            or not task_timeout <= total_timeout <= 300.0):
        raise ValueError("Frozen timeout budget is invalid")
    case_count = sum(len(v) for v in manifest.values())
    if frozen["max_case_evaluations"] != 2 * case_count:
        raise ValueError("Frozen case budget does not match the manifest")
    training_ids = [case["id"] for case in manifest["training"]]
    held_out_ids = [case["id"] for case in manifest["held_out"]]
    started = time.monotonic()
    calls = 0
    stage_number = 0

    def evaluator(stage: Path) -> dict[str, Any]:
        nonlocal calls, stage_number
        candidate = (stage / "SKILL.md").read_text(encoding="utf-8")
        expected_revision = (frozen["proposal_base"] if stage_number == 0
                             else frozen["proposal_candidate"])
        stage_number += 1
        if _sha(candidate.encode("utf-8")) != expected_revision:
            raise ValueError("Staged proposal revision moved after evaluation was frozen")
        verdicts = {}
        for partition in ("training", "held_out"):
            for case in manifest[partition]:
                remaining = total_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Offline evaluation exceeded its total deadline")
                # Purge any bytecode cache the previous case may have left,
                # then restore pinned adapter source.  The subprocess also runs
                # with -B to suppress .pyc writes, but a self-modifying adapter
                # could call py_compile/importlib directly, so we delete the
                # cache directory as a belt-and-suspenders defense.
                pycache = adapter_snapshot.parent / "__pycache__"
                if pycache.is_dir():
                    shutil.rmtree(pycache, ignore_errors=True)
                adapter_snapshot.write_bytes(adapter_bytes)
                result = _run_case(adapter_snapshot, candidate, case["input"], min(task_timeout, remaining))
                verdicts[case["id"]] = _json_equal(result, case["expected"])
                calls += 1
        return {"cases": verdicts}

    with tempfile.TemporaryDirectory(prefix="zeline-offline-adapter-") as temporary:
        adapter_snapshot = Path(temporary) / "trusted_adapter.py"
        adapter_snapshot.write_bytes(adapter_bytes)
        checks = store.test_comparison(
            frozen["proposal_id"], evaluator=evaluator,
            training_case_ids=training_ids, held_out_case_ids=held_out_ids,
            max_case_evaluations=frozen["max_case_evaluations"],
        )
    return {
        "scope": "evaluation_readiness",
        "generalization_proven": False,
        "optimizer_executed": False,
        "calls": calls,
        "manifest_sha256": frozen["manifest_sha256"],
        "adapter_sha256": frozen["adapter_sha256"],
        "checker_sha256": frozen["checker_sha256"],
        "checks": checks,
    }
