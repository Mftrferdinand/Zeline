"""Render the CLI chat screen exactly as it ships, at several widths.

Read-only preview: no network, no model call, no config writes. Used to eyeball
the blue theme and the responsive degradation on Termux (narrow) and on a
desktop terminal (broad).
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from zeline import __version__          # noqa: E402
from zeline.cli_ui import ChatUI        # noqa: E402

REPLY = """\
## Ringkasan

Deploy selesai. Tiga hal yang berubah:

| Area | Status |
|------|--------|
| Build | lulus |
| Publisher | lulus |
| PyPI | 0.4.1 live |

```python
def greet(name: str) -> str:
    return f"halo {name}"
```

Langkah berikutnya: jalankan `zeline doctor` untuk konfirmasi.
"""


def screen(width: int, color: bool, unicode_ok: bool) -> str:
    stream = io.StringIO()
    ui = ChatUI(stream=stream, width=width, color=color, unicode_ok=unicode_ok)
    ui.welcome(
        version=__version__,
        agent="Zeline",
        model="deepseek-v4.1-flash",
        provider="9Router",
        workspace="/data/data/com.termux/files/home/zeline",
        tool_profile="full (local operator)",
    )
    ui.notice("Type your message  \u2022  /help  \u2022  exit")
    ui.user("tolong cek status rilis v0.4.1")
    ui.tool("run_shell")
    ui.tool("write_file")
    ui.reply(REPLY, agent="Zeline")
    ui.question("Lanjut publish ke PyPI?", ["Ya, publish", "Tunda dulu", "Batalkan"])
    ui.answer("Ya, publish")
    ui.notice("Selesai tanpa error.", kind="info")
    return stream.getvalue()


def main() -> None:
    force = os.environ.get("FORCE_COLOR") == "1"
    for width in (40, 80, 120):
        print(f"\n================ WIDTH {width} (color={force}) ================\n")
        print(screen(width, color=force, unicode_ok=True))


if __name__ == "__main__":
    main()
