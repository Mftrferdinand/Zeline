#!/usr/bin/env python3
"""
tools/revenue_engine.py — Client Revenue Engine: bulk job runner  (v4.1.2)

Mesin buat ngebut garapan bulk tanpa ketergantungan browser. Murni stdlib;
logika inti (rate-limit, backoff, checkpoint-resume, dedupe) bisa diuji offline.

Komponen:
  - TokenBucket   : rate limiter (req/detik), clock bisa di-inject (testable).
  - backoff_delay : jadwal exponential backoff + jitter opsional.
  - Checkpoint    : state JSONL — resume dari titik gagal, idempotent.
  - dedupe        : buang duplikat (key opsional).
  - BulkRunner    : eksekusi banyak task (thread pool) + retry + rate-limit +
                    checkpoint. `worker` & `sleep` di-inject → deterministik di test.

Desain: worker = callable(task) -> value (boleh raise). Tidak ada I/O jaringan
di modul ini — jaringan ditangani worker (lihat tools/api_harvester.py).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional


class TokenBucket:
    """Token-bucket rate limiter. consume() mengembalikan detik yang harus
    ditunggu sebelum n token tersedia (0 kalau langsung boleh)."""

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec harus > 0")
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else rate_per_sec)
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def consume(self, n: float = 1.0) -> float:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return 0.0
            deficit = n - self._tokens
            self._tokens = 0.0
            return deficit / self.rate


def backoff_delay(
    attempt: int,
    *,
    base: float = 0.5,
    factor: float = 2.0,
    cap: float = 30.0,
    jitter: float = 0.0,
    rng: Optional[Callable[[], float]] = None,
) -> float:
    """Exponential backoff untuk attempt ke-0,1,2,... dengan ceiling `cap`.
    `jitter` (0..1) menambah variasi +/- fraksi; `rng` di-inject untuk test."""
    raw = min(cap, base * (factor ** max(0, attempt)))
    if jitter > 0:
        import random

        r = (rng or random.random)()
        raw = raw + (r * 2 - 1) * jitter * raw
        raw = max(0.0, min(cap, raw))
    return raw


class Checkpoint:
    """Penanda task selesai (1 key per baris di file JSONL-ish). Aman dipanggil
    dari banyak thread; resume otomatis dari isi file yang sudah ada."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._done: set[str] = set()
        self._lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                line = line.strip()
                if line:
                    self._done.add(line)

    def is_done(self, key: Any) -> bool:
        return str(key) in self._done

    def mark(self, key: Any) -> None:
        k = str(key)
        with self._lock:
            if k in self._done:
                return
            self._done.add(k)
            with self.path.open("a") as f:
                f.write(k + "\n")

    def __len__(self) -> int:
        return len(self._done)


def dedupe(
    iterable: Iterable[Any], key: Optional[Callable[[Any], Any]] = None
) -> Iterator[Any]:
    seen: set[Any] = set()
    for item in iterable:
        k = key(item) if key else item
        if k in seen:
            continue
        seen.add(k)
        yield item


@dataclass
class TaskResult:
    key: str
    ok: bool
    value: Any = None
    error: str | None = None
    attempts: int = 1


@dataclass
class RunReport:
    succeeded: list[TaskResult] = field(default_factory=list)
    failed: list[TaskResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed) + len(self.skipped)


class BulkRunner:
    """Jalanin banyak task dengan concurrency, retry/backoff, rate-limit, dan
    checkpoint-resume. `worker(task) -> value` boleh raise; kegagalan di-retry
    sampai max_retries. `sleep` di-inject biar test deterministik."""

    def __init__(
        self,
        worker: Callable[[Any], Any],
        *,
        key: Callable[[Any], Any] = str,
        max_workers: int = 8,
        max_retries: int = 3,
        rate: TokenBucket | None = None,
        checkpoint: Checkpoint | None = None,
        backoff_base: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.worker = worker
        self.key = key
        self.max_workers = max(1, int(max_workers))
        self.max_retries = max(0, int(max_retries))
        self.rate = rate
        self.checkpoint = checkpoint
        self.backoff_base = backoff_base
        self.sleep = sleep

    def _process(self, task: Any) -> tuple[str, Any]:
        k = str(self.key(task))
        if self.checkpoint and self.checkpoint.is_done(k):
            return ("skip", k)
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.rate:
                wait = self.rate.consume()
                if wait > 0:
                    self.sleep(wait)
            try:
                val = self.worker(task)
                if self.checkpoint:
                    self.checkpoint.mark(k)
                return ("ok", TaskResult(key=k, ok=True, value=val, attempts=attempt + 1))
            except Exception as e:  # noqa: BLE001 — retry semua error worker
                last_err = e
                if attempt < self.max_retries:
                    self.sleep(backoff_delay(attempt, base=self.backoff_base))
        return (
            "fail",
            TaskResult(
                key=k, ok=False, error=repr(last_err), attempts=self.max_retries + 1
            ),
        )

    def run(self, tasks: Iterable[Any]) -> RunReport:
        items = list(tasks)
        report = RunReport()
        if self.max_workers == 1:
            results = [self._process(t) for t in items]
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                results = list(ex.map(self._process, items))
        for kind, payload in results:
            if kind == "skip":
                report.skipped.append(payload)
            elif kind == "ok":
                report.succeeded.append(payload)
            else:
                report.failed.append(payload)
        return report
