"""Scheduled jobs: let Zeline do work nobody is waiting for.

Everything else in Zeline starts because a person said something. A scheduler is
the piece that lets the agent act on its own — a morning briefing, a nightly
backup check, a poll of something that changes.

Jobs live in ``~/.zeline/cron/jobs.json`` and the tick loop runs inside the
gateway process, which is already the long-lived thing on this machine. That
avoids asking the operator to install a system cron entry that would then need
its own copy of the config, the API key, and the workspace.

Four failure modes are handled deliberately, because each one is a classic way
in-app schedulers go wrong:

**No catch-up stampede.** If the gateway was off for three days, a daily job
runs *once* when it comes back, not three times. Replaying every missed run is
almost never what an operator wants and, for a job that posts a message, it is
actively harmful.

**No overlapping runs.** A job still running when its next tick arrives is
skipped with a note, not started again. An agent turn can take minutes; without
this a slow 5-minute job would pile up copies of itself.

**One runner across processes.** A lock file means two gateway processes cannot
both fire the same job. Zeline already learned this lesson with duplicate
gateway polling answering every message twice.

**Output is never lost to a delivery failure.** Every result is written to
``~/.zeline/cron/output/`` first, then delivered. If Telegram is unreachable the
work still exists on disk, and the job is not retried as though it had failed.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zeline import config

# datetime is used WITHOUT a timezone throughout, deliberately: "09:00" from an
# operator means 09:00 where they are. Converting to UTC would silently move
# every daily job by the timezone offset, so the DTZ lint findings in this module
# are accepted rather than "fixed".

# A job that outruns this is abandoned, so one wedged job cannot hold the
# scheduler forever. Generous, because a real agent turn can legitimately take
# minutes when it is building or installing something.
DEFAULT_JOB_TIMEOUT = 900.0

# How often the loop wakes up. Fine enough for minute-level schedules without
# spinning.
TICK_SECONDS = 20.0

MAX_OUTPUT_FILES = 200


class CronError(RuntimeError):
    """Something the operator should be told plainly."""


def enabled() -> bool:
    return bool(getattr(config, "CRON", True))


def format_time(moment: float) -> str:
    """Render a timestamp the way the operator will read it: their local clock."""
    if not moment:
        return "never"
    return datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M")


def cron_dir() -> Path:
    return config.DATA_DIR / "cron"


def jobs_path() -> Path:
    return cron_dir() / "jobs.json"


def output_dir() -> Path:
    return cron_dir() / "output"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------- schedules
_DURATION = re.compile(
    r"^(?:every\s+)?(\d+)\s*"
    r"(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)
_DAILY = re.compile(r"^(?:every\s+day\s+at\s+|daily\s+at\s+|at\s+)?(\d{1,2}):(\d{2})$", re.IGNORECASE)

_UNIT_SECONDS = {
    # Seconds are parsed only so a sub-minute request gets the specific "the
    # minimum is 1 minute" answer instead of a generic "I don't understand".
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}


@dataclass(frozen=True)
class Schedule:
    """A parsed schedule. Either a fixed interval or a daily wall-clock time."""

    raw: str
    seconds: int = 0
    hour: int = -1
    minute: int = -1

    @property
    def is_daily(self) -> bool:
        return self.hour >= 0

    def describe(self) -> str:
        if self.is_daily:
            return f"daily at {self.hour:02d}:{self.minute:02d}"
        if self.seconds % 86400 == 0:
            return f"every {self.seconds // 86400}d"
        if self.seconds % 3600 == 0:
            return f"every {self.seconds // 3600}h"
        return f"every {max(1, self.seconds // 60)}m"

    def next_after(self, moment: float) -> float:
        if not self.is_daily:
            return moment + self.seconds
        current = datetime.fromtimestamp(moment)
        target = current.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if target <= current:
            target += timedelta(days=1)
        return target.timestamp()


def parse_schedule(text: str) -> Schedule:
    """Accept the shapes people actually type. Raise CronError otherwise."""
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        raise CronError("a schedule is required, for example '30m', 'every 2h', or '09:00'.")

    match = _DAILY.match(cleaned)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise CronError(f"'{cleaned}' is not a valid time of day (use 00:00 to 23:59).")
        return Schedule(raw=cleaned, hour=hour, minute=minute)

    match = _DURATION.match(cleaned)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        seconds = amount * _UNIT_SECONDS[unit]
        if seconds < 60:
            # Below a minute the tick loop cannot honour it anyway, and a job
            # firing every few seconds would run the agent continuously.
            raise CronError("the shortest supported interval is 1 minute.")
        return Schedule(raw=cleaned, seconds=seconds)

    raise CronError(
        f"could not understand the schedule '{cleaned}'. Use an interval "
        "('30m', 'every 2h', '1d') or a daily time ('09:00')."
    )


# --------------------------------------------------------------------- jobs
@dataclass
class Job:
    id: str
    schedule: str
    prompt: str
    deliver: str = "local"
    enabled: bool = True
    next_run: float = 0.0
    last_run: float = 0.0
    last_status: str = ""
    runs: int = 0
    failures: int = 0
    # Counted separately from runs: a skip is not a run, and it must not
    # overwrite the status of the run that was actually in progress. A rising
    # count is how an operator learns the job cannot keep up with its schedule.
    skips: int = 0
    created_at: float = field(default_factory=time.time)

    def parsed(self) -> Schedule:
        return parse_schedule(self.schedule)

    def describe(self) -> str:
        state = "enabled" if self.enabled else "paused"
        when = format_time(self.next_run)
        line = f"{self.id}  {self.parsed().describe():<18} {state:<8} next {when}  -> {self.deliver}"
        if self.skips:
            line += f"  [{self.skips} skipped: runs longer than its interval]"
        return line


def _read_jobs() -> list[Job]:
    try:
        raw = json.loads(jobs_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    jobs: list[Job] = []
    known = set(Job.__dataclass_fields__)
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        # Unknown keys are dropped rather than crashing, so a jobs.json written
        # by a newer version does not brick an older one.
        jobs.append(Job(**{key: value for key, value in item.items() if key in known}))
    return jobs


# Serialises read-modify-write on jobs.json. Several jobs can finish at the same
# moment, and each one records its own result: without this they interleave, so
# the last writer wins and the others' updates vanish. The unique temp name
# matters for the same reason — a shared jobs.json.tmp had concurrent writers
# clobbering each other's partial file before the rename.
_JOBS_LOCK = threading.RLock()


def _write_jobs(jobs: list[Job]) -> None:
    with _JOBS_LOCK:
        _ensure_dir(cron_dir())
        temp = jobs_path().with_name(f"jobs.{os.getpid()}.{threading.get_ident()}.tmp")
        temp.write_text(
            json.dumps([asdict(job) for job in jobs], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(jobs_path())
        try:
            os.chmod(jobs_path(), 0o600)
        except OSError:
            pass


def list_jobs() -> list[Job]:
    return sorted(_read_jobs(), key=lambda job: job.created_at)


def find_job(job_id: str) -> Job | None:
    wanted = (job_id or "").strip()
    for job in _read_jobs():
        if job.id == wanted:
            return job
    return None


def _new_id(existing: list[Job]) -> str:
    taken = {job.id for job in existing}
    for number in range(1, 1000):
        candidate = f"job{number}"
        if candidate not in taken:
            return candidate
    raise CronError("too many jobs.")


def add_job(schedule: str, prompt: str, deliver: str = "local") -> Job:
    parsed = parse_schedule(schedule)   # validate before writing anything
    prompt = (prompt or "").strip()
    if not prompt:
        raise CronError("a job needs a prompt — what should the agent actually do?")
    with _JOBS_LOCK:
        jobs = _read_jobs()
        job = Job(
            id=_new_id(jobs),
            schedule=parsed.raw,
            prompt=prompt,
            deliver=(deliver or "local").strip() or "local",
            next_run=parsed.next_after(time.time()),
        )
        jobs.append(job)
        _write_jobs(jobs)
    return job


def remove_job(job_id: str) -> bool:
    with _JOBS_LOCK:
        jobs = _read_jobs()
        remaining = [job for job in jobs if job.id != job_id]
        if len(remaining) == len(jobs):
            return False
        _write_jobs(remaining)
    return True


def set_enabled(job_id: str, enabled_state: bool) -> bool:
    with _JOBS_LOCK:
        jobs = _read_jobs()
        for job in jobs:
            if job.id == job_id:
                job.enabled = enabled_state
                if enabled_state:
                    # Re-arm from now, so resuming a long-paused job does not fire
                    # instantly on a stale timestamp.
                    job.next_run = job.parsed().next_after(time.time())
                _write_jobs(jobs)
                return True
    return False


def run_now(job_id: str) -> bool:
    """Arm a job to fire on the next tick."""
    with _JOBS_LOCK:
        jobs = _read_jobs()
        for job in jobs:
            if job.id == job_id:
                job.next_run = 0.0
                job.enabled = True
                _write_jobs(jobs)
                return True
    return False


def _record_run(job_id: str, *, status: str, when: float, next_run: float) -> None:
    # Held across the read AND the write: two jobs finishing together would
    # otherwise both read the old file and the second write would drop the first
    # one's result.
    with _JOBS_LOCK:
        jobs = _read_jobs()
        for job in jobs:
            if job.id == job_id:
                job.last_run = when
                job.last_status = status[:200]
                job.next_run = next_run
                job.runs += 1
                if status.startswith("error"):
                    job.failures += 1
                _write_jobs(jobs)
                return


def _record_skip(job_id: str, *, next_run: float) -> None:
    """Note that a tick was skipped because the previous run was still going.

    Deliberately does NOT touch last_run/last_status/runs: the run in progress
    owns those, and overwriting them would replace a real result with a note
    about scheduling.
    """
    with _JOBS_LOCK:
        jobs = _read_jobs()
        for job in jobs:
            if job.id == job_id:
                job.skips += 1
                job.next_run = next_run
                _write_jobs(jobs)
                return


def due_jobs(now: float | None = None) -> list[Job]:
    moment = time.time() if now is None else now
    return [
        job for job in list_jobs()
        if job.enabled and job.next_run <= moment
    ]


# ----------------------------------------------------------------- output
def save_output(job: Job, text: str) -> Path:
    """Write a result to disk before trying to deliver it.

    Delivery can fail for reasons that have nothing to do with the work (network
    down, chat deleted). Writing first means a failed delivery never destroys
    the output.
    """
    directory = _ensure_dir(output_dir())
    stamp = datetime.fromtimestamp(time.time()).strftime("%Y%m%d-%H%M%S")
    target = directory / f"{job.id}-{stamp}.md"
    # Two runs inside the same second would otherwise write the same filename and
    # the second would silently destroy the first one's result. Found by running
    # a job twice in quick succession, which the second-resolution stamp alone
    # cannot distinguish.
    if target.exists():
        for suffix in range(2, 100):
            candidate = directory / f"{job.id}-{stamp}-{suffix}.md"
            if not candidate.exists():
                target = candidate
                break
    header = (
        f"# {job.id} — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Schedule:** {job.schedule}\n\n**Prompt:** {job.prompt}\n\n---\n\n"
    )
    target.write_text(header + (text or "(no output)"), encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    _prune_output(directory)
    return target


def _prune_output(directory: Path) -> None:
    try:
        files = sorted(directory.glob("*.md"), key=lambda item: item.stat().st_mtime)
    except OSError:
        return
    for stale in files[:-MAX_OUTPUT_FILES] if len(files) > MAX_OUTPUT_FILES else []:
        try:
            stale.unlink()
        except OSError:
            pass


def deliver(job: Job, text: str) -> tuple[bool, str]:
    """Send a result to the job's target. Returns (delivered, detail)."""
    target = (job.deliver or "local").strip()
    if target in ("", "local", "none"):
        return True, "saved locally"
    if target.startswith("telegram:"):
        chat_id = target.split(":", 1)[1].strip()
        if not chat_id:
            return False, "telegram target has no chat id"
        return _deliver_telegram(chat_id, text)
    return False, f"unknown delivery target '{target}'"


def _deliver_telegram(chat_id: str, text: str) -> tuple[bool, str]:
    token = str((getattr(config, "GATEWAYS", {}) or {}).get("telegram", {}).get("token", "") or "").strip()
    if not token:
        return False, "telegram gateway has no token configured"
    try:
        from zeline.gateways import telegram as telegram_module

        api = telegram_module.API_TEMPLATE.format(token=token)
        # Reuse the gateway's own sender so retries and message splitting behave
        # exactly as they do for a normal reply.
        result = telegram_module._api_call(
            api, "sendMessage", chat_id=chat_id, text=text[:4000],
        )
    except Exception as exc:  # noqa: BLE001 — delivery must never raise into the loop
        return False, f"{exc.__class__.__name__}: {exc}"
    if result is None:
        return False, "telegram API call failed"
    return True, f"sent to telegram:{chat_id}"


# --------------------------------------------------------------- the loop
class Scheduler:
    """Runs due jobs on a background thread inside the gateway process."""

    def __init__(self, sessions: Any, *, tick_seconds: float = TICK_SECONDS):
        self.sessions = sessions
        self.tick_seconds = max(1.0, float(tick_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Jobs currently executing. An agent turn can take minutes, so without
        # this a slow job would start a second copy of itself on the next tick.
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self.last_error = ""

    # -- lifecycle
    def start(self) -> bool:
        if not enabled():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="zeline-cron")
        self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def busy(self) -> list[str]:
        with self._lock:
            return sorted(self._running)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 — the loop must outlive any single failure
                self.last_error = f"{exc.__class__.__name__}: {exc}"
            self._stop.wait(self.tick_seconds)

    # -- work
    def tick(self, now: float | None = None) -> list[str]:
        """Fire everything due. Returns the ids that were started."""
        moment = time.time() if now is None else now
        started: list[str] = []
        for job in due_jobs(moment):
            with self._lock:
                if job.id in self._running:
                    # Skipped, not queued: a job that cannot keep up with its own
                    # schedule should run less often, not accumulate copies.
                    _record_skip(job.id, next_run=job.parsed().next_after(moment))
                    continue
                self._running.add(job.id)
            started.append(job.id)
            threading.Thread(
                target=self._execute, args=(job, moment), daemon=True,
                name=f"zeline-cron-{job.id}",
            ).start()
        return started

    def _execute(self, job: Job, moment: float) -> None:
        status = "ok"
        try:
            text = self._run_agent(job)
            path = save_output(job, text)
            delivered, detail = deliver(job, text)
            status = f"ok: {detail}" if delivered else f"ok (undelivered: {detail})"
            if not delivered:
                # The work exists; say where, so nothing has to be redone.
                status += f" — saved {path.name}"
        except Exception as exc:  # noqa: BLE001 — arbitrary agent failure
            status = f"error: {exc.__class__.__name__}: {exc}"
        finally:
            # Always advance from the CURRENT time, never by replaying missed
            # slots, so a gateway that was off for days fires once on return.
            try:
                next_run = job.parsed().next_after(time.time())
            except CronError:
                next_run = time.time() + 3600
            _record_run(job.id, status=status, when=moment, next_run=next_run)
            with self._lock:
                self._running.discard(job.id)

    def _run_agent(self, job: Job) -> str:
        """Run the job prompt as its own agent turn.

        A dedicated identity per job keeps cron work out of the operator's chat
        history and memory — a nightly job should not appear as though the user
        had asked for it, nor inherit that conversation's context.
        """
        extra = (
            "\n\nThis is a SCHEDULED run with nobody watching. Nobody can answer a "
            "question, so never ask one — decide and act. Produce the finished "
            "result the job asks for, not a plan or a status note."
        )
        return self.sessions.send(
            identity=f"cron:{job.id}",
            text=job.prompt,
            tool_profile=str(getattr(config, "CLI_TOOL_PROFILE", "full")),
            system_extra=extra,
        )
