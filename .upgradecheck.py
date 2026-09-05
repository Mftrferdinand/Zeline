"""Prove the flat→folder upgrade path: stale copy retired, customized copy kept."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

home = tempfile.mkdtemp(prefix="zlupg-")
os.environ["ZELINE_HOME"] = home

from zeline import skills  # noqa: E402

public = os.path.join(home, "skills", "public")
os.makedirs(public, exist_ok=True)

# Simulate an existing install that seeded the OLD flat skills.
for name in ("excalidraw.md", "github-auth.md", "maps.md", "p5js.md"):
    blob = subprocess.run(
        ["git", "show", f"HEAD:zeline/skills/{name}"], capture_output=True, check=True
    ).stdout
    with open(os.path.join(public, name), "wb") as handle:
        handle.write(blob)

# ...and one the user edited, which must survive.
customized = os.path.join(public, "maps.md")
with open(customized, "ab") as handle:
    handle.write(b"\n<!-- my note -->\n")

print("before upgrade:", sorted(n for n in os.listdir(public) if n.endswith(".md"))[:6])

skills.seed_skills()

after = sorted(n for n in os.listdir(public) if n.endswith(".md"))
print("stale flat copies left:", [n for n in after if n in
      {"excalidraw.md", "github-auth.md", "p5js.md"}] or "none (retired ✓)")
print("customized maps.md kept:", os.path.isfile(customized))

for name in ("excalidraw", "github-auth", "p5js", "maps"):
    folder = os.path.join(public, name, "SKILL.md")
    print(f"  folder {name:14} seeded={os.path.isfile(folder)}")

body = skills.load_skill("excalidraw")
print("\nexcalidraw resolves to the FOLDER version:",
      "references/examples.md" in body)
maps_body = skills.load_skill("maps")
print("maps still resolves to the user's customized flat copy:",
      "my note" in maps_body)

shutil.rmtree(home, ignore_errors=True)
