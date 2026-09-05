"""Structured git access that does not require handing over a shell.

Before this, every git operation went through ``run_shell``. That has two costs.

**It is owner-only.** ``run_shell`` is deliberately absent from the ``workspace``
profile, so an agent that can read and write files in a repository could not so much
as run ``git status`` — it had to guess whether its own edits were staged, and could
not show the operator a diff of what it had just changed.

**It is a string.** A path or branch name interpolated into a shell command is an
injection surface; ``git commit -m "$MSG"`` with a message the model composed is one
backtick away from executing something else. Every call here is an argument list
passed to ``subprocess`` with ``shell=False``, so a filename containing ``;`` is a
filename.

Scope is deliberate. Read operations and the two write operations that cannot lose
work (``add``, ``commit``) are here. Anything that rewrites or discards history —
push, reset, checkout of a dirty tree, clean, rebase, force anything, branch -D — is
NOT, and is refused by name with a pointer to ``run_shell``. The refusal is the
feature: an agent should not be able to destroy an operator's uncommitted work
through a tool that reads as innocuous, and the owner still has a real shell when
they genuinely mean it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

#: Read-only inspection, safe on any repository.
READ_ACTIONS = ("status", "diff", "log", "show", "branch")
#: Writes that only ever ADD state; nothing here can lose committed or working data.
WRITE_ACTIONS = ("add", "commit")
ACTIONS = READ_ACTIONS + WRITE_ACTIONS

#: Refused with a specific explanation rather than a generic "unknown action", so
#: the model learns the boundary instead of retrying variations of the same verb.
REFUSED = {
    "push": "publishes to a remote",
    "pull": "merges remote work into the tree",
    "fetch": "is only useful with push/pull, which are refused",
    "reset": "can discard committed and staged work",
    "revert": "writes history; do it deliberately in a shell",
    "rebase": "rewrites history",
    "merge": "can leave a conflicted tree the agent cannot resolve blind",
    "checkout": "can silently overwrite uncommitted changes",
    "switch": "can silently overwrite uncommitted changes",
    "restore": "discards uncommitted changes",
    "clean": "deletes untracked files irreversibly",
    "stash": "hides changes somewhere the operator will not look",
    "cherry-pick": "writes history",
    "tag": "publishes a release marker",
    "rm": "deletes tracked files",
    "mv": "renames tracked files",
    "config": "changes the operator's git identity or hooks",
    "remote": "repoints where code is published",
    "submodule": "fetches and checks out other repositories",
    "worktree": "creates checkouts outside the workspace",
    "gc": "rewrites the object store",
    "filter-branch": "rewrites history",
    "init": "creating a repository is a decision, not a step",
    "clone": "fetches a whole remote repository",
}

TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 12_000
MAX_MESSAGE_CHARS = 2_000

#: Files whose contents are usually credentials. Staging one is almost always a
#: mistake the operator only discovers after it is pushed, so ``add`` names them and
#: ``commit`` refuses while any is staged.
_SECRET_PATTERNS = (
    re.compile(r"(^|/)\.env(\.|$)"),
    re.compile(r"(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.|$)"),
    re.compile(r"\.(pem|key|pfx|p12|keystore|jks)$"),
    re.compile(r"(^|/)(credentials|secrets?|token|tokens)(\.json|\.yml|\.yaml|\.txt)?$"),
    re.compile(r"(^|/)\.npmrc$|(^|/)\.pypirc$|(^|/)\.netrc$"),
    re.compile(r"service[-_]?account.*\.json$"),
)


class GitError(RuntimeError):
    """Something the caller should be told plainly."""


def looks_secret(path: str) -> bool:
    lowered = (path or "").replace("\\", "/").casefold()
    return any(pattern.search(lowered) for pattern in _SECRET_PATTERNS)


def _run(args: list[str], cwd: Path, *, strip: bool = True) -> tuple[int, str]:
    """Run one git command as an argument list. Never through a shell.

    ``strip=False`` matters for ``--porcelain``: its status codes live in the first
    two columns and an unstaged modification is spelled with a LEADING SPACE
    (`" M a.py"`). Stripping the combined output eats that space, which shifts the
    first line's code left by one — so ` M` reads as `M ` (staged), and the path
    loses its first character (`a.py` → `.py`). Both bugs were real: `commit`
    believed an unstaged file was staged and handed git a commit it had to reject.
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            # A prompt for credentials or an editor would hang the turn forever with
            # nobody able to answer it.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"},
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {TIMEOUT_SECONDS}s.") from exc
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output.strip() if strip else output


def _require_repo(cwd: Path) -> None:
    code, output = _run(["rev-parse", "--is-inside-work-tree"], cwd)
    if code != 0 or output.strip() != "true":
        raise GitError(
            f"{cwd} is not inside a git repository. Nothing here creates one — that is "
            "a decision for the operator."
        )


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text or "(no output)"
    return text[:limit] + f"\n… [truncated, {len(text) - limit} more characters]"


def _resolve_paths(paths: str, workspace: Path) -> list[str]:
    """Split a comma/space separated path list and keep it inside the workspace."""
    raw = [item.strip() for item in re.split(r"[,\n]+", paths or "") if item.strip()]
    if not raw:
        raise GitError("give at least one path to stage, or '.' for everything tracked.")
    resolved: list[str] = []
    for item in raw:
        if item == ".":
            resolved.append(".")
            continue
        candidate = (workspace / item).resolve(strict=False)
        if not candidate.is_relative_to(workspace.resolve(strict=False)):
            raise GitError(f"path must stay inside the workspace: {item}")
        resolved.append(item)
    return resolved


def _status_lines(cwd: Path) -> list[tuple[str, str]]:
    """(xy, path) for each entry of ``git status --porcelain``.

    Read unstripped: the two status columns are positional and an unstaged change
    begins with a space.
    """
    _, output = _run(["status", "--porcelain=v1"], cwd, strip=False)
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        # A rename is reported as "R  old -> new"; the new name is what is staged.
        path = line[3:].strip().strip('"')
        if line[:2].startswith(("R", "C")) and " -> " in path:
            path = path.split(" -> ", 1)[1].strip().strip('"')
        entries.append((line[:2], path))
    return entries


def _current_branch(cwd: Path) -> str:
    """Branch name, including in a repository with no commits yet.

    ``rev-parse --abbrev-ref HEAD`` prints "HEAD" and a fatal error before the first
    commit, and that error was landing in the operator's status output. `git branch
    --show-current` answers correctly on an unborn branch.
    """
    code, output = _run(["branch", "--show-current"], cwd)
    name = output.strip()
    if code == 0 and name:
        return name
    return "(no branch)"


def status(cwd: Path) -> str:
    _require_repo(cwd)
    branch = _current_branch(cwd)
    entries = _status_lines(cwd)
    if not entries:
        return f"On branch {branch}. Working tree clean."
    staged = [path for code, path in entries if code[0] not in " ?"]
    unstaged = [path for code, path in entries if code[1] != " " and code != "??"]
    untracked = [path for code, path in entries if code == "??"]
    parts = [f"On branch {branch}."]
    for label, group in (
        ("Staged", staged),
        ("Not staged", unstaged),
        ("Untracked", untracked),
    ):
        if group:
            shown = group[:40]
            more = f" (+{len(group) - len(shown)} more)" if len(group) > len(shown) else ""
            parts.append(f"{label} ({len(group)}){more}:\n" + "\n".join(f"  {p}" for p in shown))
    secrets = sorted({path for path in staged if looks_secret(path)})
    if secrets:
        parts.append("WARNING: staged files that usually hold credentials: " + ", ".join(secrets))
    return "\n".join(parts)


def diff(cwd: Path, *, staged: bool = False, path: str = "") -> str:
    _require_repo(cwd)
    args = ["diff", "--stat", "--patch"]
    if staged:
        args.append("--cached")
    if path:
        args.extend(["--", *_resolve_paths(path, cwd)])
    code, output = _run(args, cwd)
    if code != 0:
        raise GitError(output or "git diff failed.")
    if not output:
        where = "staged" if staged else "unstaged"
        return f"No {where} changes{f' under {path}' if path else ''}."
    return _clip(output)


def log(cwd: Path, *, limit: int = 10, path: str = "") -> str:
    _require_repo(cwd)
    count = max(1, min(int(limit or 10), 100))
    args = ["log", f"-{count}", "--pretty=format:%h %ad %an: %s", "--date=short"]
    if path:
        args.extend(["--", *_resolve_paths(path, cwd)])
    code, output = _run(args, cwd)
    if code != 0:
        raise GitError(output or "git log failed.")
    return _clip(output or "(no commits)")


def show(cwd: Path, *, ref: str = "HEAD") -> str:
    _require_repo(cwd)
    clean = (ref or "HEAD").strip()
    # A ref is not a path or a flag: rejecting the shapes that are neither keeps a
    # crafted "ref" from turning into an option git would honour.
    if not re.fullmatch(r"[\w./^~@{}-]{1,120}", clean) or clean.startswith("-"):
        raise GitError(f"'{ref}' is not a plausible git ref.")
    code, output = _run(["show", "--stat", "--patch", clean], cwd)
    if code != 0:
        raise GitError(output or f"no such ref: {clean}")
    return _clip(output)


def branch(cwd: Path) -> str:
    _require_repo(cwd)
    current = _current_branch(cwd)
    _, listing = _run(["branch", "--list", "--format=%(refname:short)"], cwd)
    names = [line.strip() for line in listing.splitlines() if line.strip()]
    body = "\n".join(f"{'*' if name == current else ' '} {name}" for name in names)
    return f"Current branch: {current}\n{body}" if names else f"Current branch: {current}"


def add(cwd: Path, *, path: str) -> str:
    _require_repo(cwd)
    targets = _resolve_paths(path, cwd)
    code, output = _run(["add", "--", *targets], cwd)
    if code != 0:
        raise GitError(output or "git add failed.")
    entries = _status_lines(cwd)
    staged = [item for item, name in entries if item[0] not in " ?"]
    secrets = sorted(
        {name for code_pair, name in entries if code_pair[0] not in " ?" and looks_secret(name)}
    )
    note = f"Staged {len(staged)} file(s)."
    if secrets:
        note += (
            "\nWARNING: these look like credential files and are now staged: "
            + ", ".join(secrets)
            + "\nUnstage them before committing unless the operator explicitly said to include them."
        )
    return note


def commit(cwd: Path, *, message: str) -> str:
    _require_repo(cwd)
    text = (message or "").strip()
    if not text:
        raise GitError("a commit needs a message.")
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS]
    entries = _status_lines(cwd)
    staged = [name for code_pair, name in entries if code_pair[0] not in " ?"]
    if not staged:
        raise GitError(
            "nothing is staged. Use action='add' with the specific paths first — "
            "staging everything blindly commits unrelated work."
        )
    secrets = sorted({name for name in staged if looks_secret(name)})
    if secrets:
        # Refusing beats warning: once this is committed the operator has to rewrite
        # history to undo it, which is exactly what this module will not do.
        raise GitError(
            "refusing to commit files that look like credentials: "
            + ", ".join(secrets)
            + ". Unstage them (git restore --staged <path> in a shell), or have the "
            "operator confirm they belong in the repository."
        )
    code, output = _run(["commit", "-m", text], cwd)
    if code != 0:
        # git's own failure text is a screenful of hints aimed at a human at a
        # terminal; the useful line is usually the last one.
        detail = (output or "git commit failed.").strip().splitlines()
        raise GitError(detail[-1][:300] if detail else "git commit failed.")
    _, head = _run(["rev-parse", "--short", "HEAD"], cwd)
    return f"Committed {head} with {len(staged)} file(s).\n{_clip(output, 2_000)}"
