"""
tools/desktop_control.py — macOS Desktop Control (background, NO cursor steal)  (v4.1)

Drive app macOS lewat AppleScript/Accessibility TANPA ngerebut kursor fisik atau
fokus operator. Bukan PyAutoGUI/cliclick (yang mindahin pointer global).

Kontrak utama:
- background-first: target app by name, JANGAN activate kecuali diminta eksplisit.
- gak ada screenshot/keylog diam-diam.
- aksi destruktif (hapus/kirim/post) → R9 gate sekali (confirm_cb).
- butuh izin: System Settings → Privacy → Accessibility & Automation (sekali, lalu diam).

macOS-only (butuh `osascript`). Pakai:
    from desktop_control import run_applescript, app_keystroke, app_menu, notify
    notify("Agent", "task selesai")
    app_keystroke("Notes", "halo")           # tanpa mindahin kursor
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Optional

DESTRUCTIVE_HINTS = ("delete", "hapus", "send", "kirim", "post", "quit", "shut down", "trash")


def _osascript_available() -> bool:
    return shutil.which("osascript") is not None


def run_applescript(script: str, confirm_cb: Optional[Callable[[str], bool]] = None) -> str:
    """Jalankan AppleScript. Kalau script kelihatan destruktif → minta confirm dulu (R9).

    confirm_cb(msg) → True utk lanjut. None = tanpa gate (pakai utk aksi non-destruktif saja).
    """
    if not _osascript_available():
        raise RuntimeError("osascript tidak ada — desktop_control macOS-only.")
    low = script.lower()
    if any(h in low for h in DESTRUCTIVE_HINTS):
        if confirm_cb is None or not confirm_cb(f"⚠️ AppleScript destruktif:\n{script[:200]}\nLanjut?"):
            return "[ABORTED] butuh konfirmasi operator (R9)"
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"osascript error: {res.stderr.strip()}")
    return res.stdout.strip()


def app_keystroke(app: str, keys: str) -> str:
    """Kirim keystroke ke proses `app` TANPA mindahin kursor/fokus global."""
    script = (f'tell application "System Events" to tell process "{app}" '
              f'to keystroke "{keys}"')
    return run_applescript(script)


def app_menu(app: str, menu: str, item: str) -> str:
    """Klik menu item app target via Accessibility (gak ganggu pointer)."""
    script = (f'tell application "System Events" to tell process "{app}" '
              f'to click menu item "{item}" of menu "{menu}" of menu bar 1')
    return run_applescript(script)


def app_ax_press(app: str, button_index: int = 1, window_index: int = 1,
                 confirm_cb: Optional[Callable[[str], bool]] = None) -> str:
    """AXPress tombol di window app tanpa raise window (frontmost tetap false)."""
    script = (f'tell application "System Events" to tell process "{app}" '
              f'to perform action "AXPress" of button {button_index} of window {window_index}')
    return run_applescript(script, confirm_cb)


def notify(title: str, message: str, sound: bool = False) -> str:
    """Notifikasi macOS — non-intrusif, gak rebut fokus."""
    s = f' sound name "Glass"' if sound else ""
    return run_applescript(f'display notification "{message}" with title "{title}"{s}')


def is_app_running(app: str) -> bool:
    out = run_applescript(
        f'tell application "System Events" to (name of processes) contains "{app}"')
    return out.strip().lower() == "true"


if __name__ == "__main__":
    if _osascript_available():
        print(notify("desktop_control", "smoke test OK"))
    else:
        print("osascript tidak ada (bukan macOS) — skip smoke test.")
