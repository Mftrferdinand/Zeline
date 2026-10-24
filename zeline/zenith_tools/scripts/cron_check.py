#!/usr/bin/env python3
"""
tools/cron_check.py — Cron Expression Validator & Human Description  (v1.0)

Zeline punya scheduler harian/interval, tapi agent yang nulis ekspresi cron
buat user sering salah ketik dan salahnya baru keliatan pas job gak jalan.
Tool ini validasi ekspresi cron 5-field TANPA subprocess `crontab` (gak ada
di Termux/CI), terus jelasin artinya dalam bahasa manusia.

Zero dependency, pure functions.

API:
- parse(expr) -> CronSpec | raises CronError
- describe(expr) -> str ringkasan manusiawi
- next_matches(expr, dt) -> bool apakah dt cocok dengan ekspresi
- validate(expr) -> dict {"ok": bool, "error"?: str, "description"?: str}
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

_DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_MONTH = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

_FIELDS = ("minute", "hour", "day_of_month", "month", "day_of_week")
_BOUNDS = {"minute": (0, 59), "hour": (0, 23), "day_of_month": (1, 31),
           "month": (1, 12), "day_of_week": (0, 7)}

_DOW_ALIASES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
_MONTH_ALIASES = {m.lower()[:3]: i for i, m in enumerate(_MONTH) if m}


class CronError(ValueError):
    """Raised for malformed cron expressions."""


@dataclass
class CronSpec:
    minute: set[int]
    hour: set[int]
    day_of_month: set[int]
    month: set[int]
    day_of_week: set[int]   # normalized 0-6, 7 mapped to 0
    dom_star: bool
    dow_star: bool

    def matches(self, dt: datetime) -> bool:
        if dt.minute not in self.minute or dt.hour not in self.hour:
            return False
        if dt.month not in self.month:
            return False
        # POSIX cron rule: when both DOM and DOW are restricted, either may
        # fire. When one is '*', only the other restricts.
        dom_ok = dt.day in self.day_of_month
        dow_ok = ((dt.weekday() + 1) % 7) in self.day_of_week  # cron: Sun=0..Sat=6
        if self.dom_star and self.dow_star:
            return True
        if self.dom_star:
            return dow_ok
        if self.dow_star:
            return dom_ok
        return dom_ok or dow_ok


def _parse_atom(atom: str, low: int, high: int, aliases: dict[str, int] | None,
                field: str) -> set[int]:
    """Parse one comma-separated atom: *, N, N-M, */S, N-M/S, aliased names."""
    atom = atom.strip()
    aliases = aliases or {}
    step = 1
    if "/" in atom:
        atom, step_s = atom.split("/", 1)
        if not step_s.isdigit() or int(step_s) < 1:
            raise CronError(f"{field}: invalid step '/{step_s}'")
        step = int(step_s)
    if atom == "*":
        start, end = low, high
    elif "-" in atom.lstrip("-"):
        a, b = atom.split("-", 1)
        start = aliases.get(a.lower(), int(a) if a.isdigit() else None) if a else None
        end = aliases.get(b.lower(), int(b) if b.isdigit() else None) if b else None
        if a.isdigit():
            start = int(a)
        if b.isdigit():
            end = int(b)
        if start is None or end is None:
            raise CronError(f"{field}: invalid range '{atom}'")
    elif atom.isdigit():
        start = end = int(atom)
    elif atom.lower() in aliases:
        start = end = aliases[atom.lower()]
    else:
        raise CronError(f"{field}: invalid value '{atom}'")
    if not (low <= start <= high and low <= end <= high):
        raise CronError(f"{field}: value(s) of '{atom}' out of range {low}-{high}")
    if start > end:
        raise CronError(f"{field}: reversed range '{atom}'")
    return set(range(start, end + 1, step))


def _parse_field(raw: str, field: str) -> set[int]:
    low, high = _BOUNDS[field]
    aliases = _DOW_ALIASES if field == "day_of_week" else \
        _MONTH_ALIASES if field == "month" else {}
    out: set[int] = set()
    for atom in raw.split(","):
        if not atom:
            raise CronError(f"{field}: empty element in '{raw}'")
        out |= _parse_atom(atom, low, high, aliases, field)
    if field == "day_of_week":
        out = {v % 7 for v in out}  # 7 == Sunday == 0
    return out


def parse(expr: str) -> CronSpec:
    """Parse a 5-field cron expression; raises CronError with a clear message."""
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(
            f"expected 5 fields (minute hour dom month dow), got {len(fields)}")
    dom = _parse_field(fields[2], "day_of_month")
    dow = _parse_field(fields[4], "day_of_week")
    return CronSpec(
        minute=_parse_field(fields[0], "minute"),
        hour=_parse_field(fields[1], "hour"),
        day_of_month=dom,
        month=_parse_field(fields[3], "month"),
        day_of_week=dow,
        dom_star=fields[2] == "*",
        dow_star=fields[4] == "*",
    )


def describe(expr: str) -> str:
    """One-line human description of a valid cron expression."""
    spec = parse(expr)
    if spec.minute == {0} and spec.hour == {0} and spec.dom_star and \
            spec.dow_star and spec.month == set(range(1, 13)):
        return "Every day at 00:00"
    if len(spec.hour) == 1 and spec.dom_star and spec.dow_star:
        h = next(iter(spec.hour))
        return (f"Every day at {h:02d}:{next(iter(spec.minute)):02d}"
                if len(spec.minute) == 1 else
                f"Every day at {h:02d}: minutes {sorted(spec.minute)}")
    if spec.minute == {0} and spec.dow_star and spec.dom_star and \
            len(spec.hour) < 24:
        return f"Hourly at minute 0 (hours {sorted(spec.hour)})" if len(spec.hour) < 12 \
            else "Hourly at minute 0"
    days = sorted(_DOW[d] for d in spec.day_of_week)
    if not spec.dow_star and days:
        return f"On {', '.join(days)} at {sorted(spec.hour)}:{sorted(spec.minute)}"
    months = sorted(_MONTH[m] for m in spec.month)
    return (f"Minutes {sorted(spec.minute)}, hours {sorted(spec.hour)}, "
            f"days-of-month {sorted(spec.day_of_month)}, months {months}")


def next_matches(expr: str, dt: datetime) -> bool:
    """True when the given datetime would fire under the expression."""
    return parse(expr).matches(dt)


def next_run(expr: str, after: Optional[datetime] = None,
             horizon_days: int = 400) -> Optional[datetime]:
    """Next firing time after `after` (inclusive of the next minute), or None."""
    spec = parse(expr)
    dt = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = dt + timedelta(days=horizon_days)
    while dt < limit:
        if spec.matches(dt):
            return dt
        dt += timedelta(minutes=1)
    return None


def validate(expr: str) -> dict:
    """Structured result for agent consumption: ok / error / description."""
    try:
        nxt = next_run(expr)
        return {"ok": True, "description": describe(expr),
                "next_run": nxt.isoformat() if nxt else None}
    except CronError as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    """CLI: python3 -m zeline.zenith_tools.scripts.cron_check '*/5 * * * *'"""
    import json
    import sys
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    print(json.dumps(validate(sys.argv[1]), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
