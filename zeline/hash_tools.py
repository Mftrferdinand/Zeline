"""Hash and checksum helper for workspace files.

Before this, an agent that downloaded a release asset or wrote a file could
only verify a checksum by composing a shell command — which the ``workspace``
profile does not have. ``hash_check`` gives every profile a read-only way to
compute and compare SHA-256/SHA-1/MD5 digests, the two steps of "did this
download match what the README says": hash a file, compare against an
expected digest.

Deliberate scope:

- **Workspace-confined.** The same ``_resolve_workspace_path`` fence as every
  other file tool; no absolute paths outside the workspace.
- **Read-only.** Hashing cannot modify anything, so it is available on the
  ``safe`` profile like ``read_file``.
- **Compare in constant-ish shape.** The expected digest is casefolded and
  length-checked before comparison; the result says MATCH or MISMATCH with
  both digests shown, so a mismatch is evidence, not a bare "no".
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ALGORITHMS = ("sha256", "sha1", "md5")
READ_CHUNK = 65536


def _hash_check(
    path: str,
    workspace: Path,
    algorithm: str = "sha256",
    expected: str = "",
) -> str:
    """Hash a workspace file, optionally comparing against ``expected``."""
    # Imported here, not at module level: zeline.tools imports this module into
    # its registry, and the path fence lives in tools itself.
    from zeline.tools import _resolve_workspace_path

    algo = (algorithm or "sha256").strip().lower().replace("-", "")
    if algo not in ALGORITHMS:
        return f"ERROR hash_check: algorithm must be one of {', '.join(ALGORITHMS)}."
    try:
        target = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR hash_check: {exc}"
    if not target.is_file():
        return f"ERROR hash_check: not a file or not found: {target}"
    try:
        digest = hashlib.new(algo)
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(READ_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        return f"ERROR hash_check: {exc.__class__.__name__}: {exc}"
    actual = digest.hexdigest()
    wanted = str(expected or "").strip().lower().replace(" ", "")
    if not wanted:
        return f"{algo}: {actual}\n{target}"
    if len(wanted) != len(actual) or any(
        char not in "0123456789abcdef" for char in wanted
    ):
        return (
            f"ERROR hash_check: expected value is not a valid {algo} digest "
            f"({len(actual)} hex characters)."
        )
    if wanted == actual:
        return f"MATCH: {target}\n  expected {algo}: {wanted}\n  actual   {algo}: {actual}"
    return (
        f"MISMATCH: {target}\n"
        f"  expected {algo}: {wanted}\n  actual   {algo}: {actual}"
    )
