"""CI guard: the PID liveness probe must not kill the process it checks.

Runs on Windows in GitHub Actions (see .github/workflows/tests.yml).

Background: the POSIX idiom for "is this PID alive?" is ``os.kill(pid, 0)`` --
signal 0 is a permission/existence probe that delivers nothing. On Windows,
CPython maps ``os.kill`` to ``TerminateProcess`` and IGNORES the signal
argument, so the same line silently kills the target. Before the Windows port,
``zeline gateway status`` would therefore have killed the gateway it was asked
to report on.

``gateway_service._pid_alive`` must branch to ``_winproc.pid_alive``
(OpenProcess + GetExitCodeProcess) on Windows. This script proves it does, by
probing a real child process repeatedly and asserting the child survives.

Lives in a file rather than inline in the workflow because PowerShell has no
heredoc syntax.
"""

from __future__ import annotations

import subprocess
import sys

from zeline import gateway_service


def main() -> int:
    if not gateway_service.IS_WINDOWS:
        print("SKIP: not Windows; this guard only applies to the Windows path")
        return 0

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Probe several times: a destructive probe shows up on the first pass,
        # but repeating it also catches handle-leak style flakiness.
        for attempt in range(1, 4):
            alive = gateway_service._pid_alive(child.pid)
            if not alive:
                print(f"FAIL: probe {attempt} reported PID {child.pid} as dead")
                return 1
            if child.poll() is not None:
                print(
                    f"FAIL: probe {attempt} TERMINATED PID {child.pid} "
                    f"(exit {child.returncode}) -- os.kill leaked into the "
                    "Windows path"
                )
                return 1

        token = gateway_service._process_start_token(child.pid)
        if not token or not token.startswith("wincreate:"):
            print(f"FAIL: expected a 'wincreate:' start token, got {token!r}")
            return 1

        # A PID that does not exist must read as dead, or the probe is useless.
        if gateway_service._pid_alive(0xFFFF_FFF0):
            print("FAIL: probe reported a bogus PID as alive")
            return 1

        print(f"OK: probe is non-destructive; start token = {token}")
        return 0
    finally:
        child.kill()
        child.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
