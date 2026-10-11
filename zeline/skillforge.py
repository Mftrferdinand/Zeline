"""SkillForge: Zeline's self-improving skill engine.

The closed learning loop, done better than the reference implementations:

1. **Effectiveness scoring** — every skill carries a 0-100 score. Successful
   reuse raises it, failed reuse lowers it, disuse decays it slowly.
   Skills below threshold are auto-deprecated. (Reference impls track
   existence, not effectiveness.)
2. **Semantic retrieval** — incoming tasks are matched to skills via local
   ONNX embeddings (``zeline.embeddings``), falling back to keyword overlap
   when embeddings are unavailable.
3. **Duplicate detection** — before saving, a new skill is compared against
   existing ones by embedding similarity. Near-duplicates (>0.85) are
   merged instead of saved separately.
4. **Versioning** — skill updates bump a version counter and keep the
   previous content, so a bad update can be rolled back.
5. **Decay + auto-deprecation** — stale, unused, low-score skills are moved
   aside automatically instead of rotting in the library.

Storage reuses ``zeline.learning.learned_dir()`` (``~/.zeline/skills/learned/``)
so existing skills keep working. Each skill directory gains a ``meta.json``
sidecar; skills without one get default metadata on first touch.

All public functions are total: they never raise. Failures return ``None``,
``False``, or empty collections so the agent loop stays alive.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Score given to a brand-new skill.
INITIAL_SCORE = 50.0
#: Points added on each successful reuse.
SCORE_ON_SUCCESS = 5.0
#: Points removed on each failed reuse.
SCORE_ON_FAILURE = 10.0
#: Points lost per week of disuse.
DECAY_PER_WEEK = 2.0
#: Below this score a skill is auto-deprecated.
DEPRECATE_THRESHOLD = 20.0
#: Cosine similarity above this means "probably the same skill".
DUPLICATE_THRESHOLD = 0.85
#: Cosine similarity above this (but below duplicate) means "related".
RELATED_THRESHOLD = 0.60
#: Meta sidecar filename inside each skill directory.
META_FILE = "meta.json"
#: Ledger of every SkillForge action.
LEDGER_FILE = "skillforge-ledger.jsonl"

_lock = threading.RLock()  # RLock: record_use holds it across save_meta/get_meta calls


def _xproc_lock_path() -> Path | None:
    """Path to the cross-process lock file."""
    base = _learned_dir()
    return (base / ".skillforge.lock") if base else None


class _xproc_lock:
    """Cross-process exclusive lock via fcntl.flock.

    threading.RLock only serializes threads within one process. When the
    gateway and CLI (or two gateway workers) run concurrently, increments
    are lost without an OS-level lock. Best-effort: if fcntl is unavailable
    (non-POSIX) or the lock file can't be created, degrade to no-op rather
    than crashing.
    """

    def __init__(self):
        self._fh = None
        self._path = None

    def __enter__(self):
        try:
            self._path = _xproc_lock_path()
            if self._path is None:
                return self
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "w")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._fh = None
        return self

    def __exit__(self, *args):
        try:
            if self._fh is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                self._fh.close()
        except Exception:
            pass
        self._fh = None
        return False


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SkillMeta:
    """Scoring + versioning metadata for one learned skill."""

    slug: str
    name: str = ""
    version: int = 1
    score: float = INITIAL_SCORE
    use_count: int = 0
    success_count: int = 0
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    last_used_ts: float = 0.0
    tags: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    embedding_model: str = ""
    deprecated: bool = False
    previous_content: str = ""  # last version, for rollback

    def to_dict(self) -> dict:
        d = asdict(self)
        # Don't persist huge embeddings twice; keep them, they're small (384 floats).
        return d

    @classmethod
    def from_dict(cls, slug: str, d: dict) -> "SkillMeta":
        try:
            return cls(
                slug=slug,
                name=str(d.get("name", "")),
                version=int(d.get("version", 1)),
                score=float(d.get("score", INITIAL_SCORE)),
                use_count=int(d.get("use_count", 0)),
                success_count=int(d.get("success_count", 0)),
                created_ts=float(d.get("created_ts", time.time())),
                updated_ts=float(d.get("updated_ts", time.time())),
                last_used_ts=float(d.get("last_used_ts", 0.0)),
                tags=list(d.get("tags", [])),
                embedding=d.get("embedding"),
                embedding_model=str(d.get("embedding_model", "")),
                deprecated=bool(d.get("deprecated", False)),
                previous_content=str(d.get("previous_content", "")),
            )
        except (TypeError, ValueError):
            return cls(slug=slug)


# ---------------------------------------------------------------------------
# Paths & low-level IO
# ---------------------------------------------------------------------------


def _learned_dir() -> Path | None:
    try:
        from zeline import learning as _learning

        return _learning.learned_dir()
    except Exception:
        return None


def _skill_dir(slug: str) -> Path | None:
    base = _learned_dir()
    if base is None:
        return None
    # Reuse learning's slugify to stay consistent.
    try:
        from zeline import learning as _learning

        safe = _learning._slugify(slug)
    except Exception:
        safe = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:100] or "skill"
    d = base / safe
    # Refuse symlinks (could point outside the skills tree).
    if d.is_symlink():
        return None
    return d if d.is_dir() else None


def _meta_path(slug: str) -> Path | None:
    d = _skill_dir(slug)
    return (d / META_FILE) if d else None


def _ledger_path() -> Path | None:
    base = _learned_dir()
    return (base / LEDGER_FILE) if base else None


def _log(action: str, slug: str, detail: str = "") -> None:
    try:
        p = _ledger_path()
        if p is None:
            return
        entry = {
            "ts": time.time(),
            "action": action,
            "slug": slug,
            "detail": detail,
        }
        # Hold the module lock so concurrent writers don't interleave lines.
        # (RLock: safe even when called from within a locked section.)
        with _lock:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # Ledger holds skill names/scores: keep it owner-only like meta.json.
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass
    except Exception:
        pass


def get_meta(slug: str) -> SkillMeta | None:
    """Load metadata for a skill; creates defaults if the skill exists but has no meta."""
    try:
        mp = _meta_path(slug)
        if mp is None:
            return None
        if mp.is_file():
            d = json.loads(mp.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return SkillMeta.from_dict(slug, d)
        # Skill exists but no meta yet — synthesize defaults.
        d = _skill_dir(slug)
        name = d.name if d else slug
        meta = SkillMeta(slug=slug, name=name)
        save_meta(meta)
        return meta
    except Exception:
        return None


def save_meta(meta: SkillMeta) -> bool:
    """Persist metadata. Never raises."""
    try:
        mp = _meta_path(meta.slug)
        if mp is None:
            return False
        with _lock:
            mp.write_text(
                json.dumps(meta.to_dict(), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        try:
            mp.chmod(0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _all_skill_dirs() -> list[Path]:
    """All directories that hold operator/auto-learned skills (private + learned)."""
    dirs: list[Path] = []
    try:
        from zeline import skills as _skills

        for d in (_skills.PRIVATE_SKILLS_DIR, _skills.LEARNED_SKILLS_DIR):
            if d.is_dir():
                dirs.append(d)
    except Exception:
        pass
    base = _learned_dir()
    if base is not None and base not in dirs:
        dirs.append(base)
    # Deduplicate preserving order.
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _iter_all_skills() -> list[tuple[str, Path]]:
    """Return (slug, skill_dir) across all skill directories."""
    out: list[tuple[str, Path]] = []
    for base in _all_skill_dirs():
        try:
            for child in sorted(base.iterdir()):
                # Skip dot-directories (.archive, .git, etc.) and symlinks.
                if child.name.startswith("."):
                    continue
                if child.is_symlink():
                    continue
                if child.is_dir():
                    out.append((child.name, child))
        except Exception:
            continue
    return out


def _find_skill_dir_anywhere(slug: str) -> Path | None:
    """Find a skill directory by slug across all skill dirs."""
    for s, d in _iter_all_skills():
        if s == slug:
            return d
    return None


def _read_skill_text(slug: str) -> str:
    """Best-effort read of a skill's SKILL.md body for embedding."""
    try:
        d = _find_skill_dir_anywhere(slug)
        if d is None:
            d = _skill_dir(slug)  # fallback to learned/
        if d is None:
            return ""
        md = d / "SKILL.md"
        if md.is_file():
            return md.read_text(encoding="utf-8")[:2000]
        # Legacy single-file format.
        for f in d.glob("*.md"):
            return f.read_text(encoding="utf-8")[:2000]
    except Exception:
        pass
    return ""


def _read_skill_text_full(slug: str) -> str:
    """Read the FULL SKILL.md body (for rollback snapshots — no truncation)."""
    try:
        d = _find_skill_dir_anywhere(slug)
        if d is None:
            d = _skill_dir(slug)
        if d is None:
            return ""
        md = d / "SKILL.md"
        if md.is_file() and not md.is_symlink():
            return md.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _embed_text(text: str) -> tuple[list[float] | None, str]:
    """Embed text, returning (vector, model_name). Vector None on failure."""
    try:
        from zeline import embeddings as _emb

        if not _emb.embeddings_available():
            return None, ""
        embedder = _emb.get_embedder()
        if embedder is None:
            return None, ""
        vecs = embedder.embed([text[:1000]])
        if vecs and vecs[0]:
            return vecs[0], getattr(embedder, "model_name", "")
    except Exception:
        pass
    return None, ""


def _valid_cached_embedding(meta: SkillMeta) -> list[float] | None:
    """Return cached embedding only if it matches the current model."""
    try:
        from zeline import embeddings as _emb

        embedder = _emb.get_embedder()
        current_model = getattr(embedder, "model_name", "") if embedder else ""
        expected_dim = getattr(embedder, "dim", 0) if embedder else 0
        if (
            meta.embedding
            and meta.embedding_model == current_model
            and current_model
            # Also validate dimensions: if the model was replaced in place
            # (same name, new dims), the stale vector would silently score 0.0
            # forever without ever being re-embedded.
            and (not expected_dim or len(meta.embedding) == expected_dim)
        ):
            return meta.embedding
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def record_use(slug: str, success: bool) -> float | None:
    """Record one reuse of a skill. Returns the new score, or None on failure.

    Success → +SCORE_ON_SUCCESS (cap 100). Failure → -SCORE_ON_FAILURE
    (floor 0). Updates use/success counters and last-used timestamp.
    The read-modify-write is done under both the thread lock and a
    cross-process file lock so concurrent feedback calls (even from
    separate processes) don't lose increments.
    """
    try:
        with _xproc_lock(), _lock:
            meta = get_meta(slug)
            if meta is None or meta.deprecated:
                return None
            meta.use_count += 1
            meta.last_used_ts = time.time()
            meta.updated_ts = time.time()
            if success:
                meta.success_count += 1
                meta.score = min(100.0, meta.score + SCORE_ON_SUCCESS)
            else:
                meta.score = max(0.0, meta.score - SCORE_ON_FAILURE)
            # If the write fails, report failure (don't lie about the score).
            if not save_meta(meta):
                return None
            _log("use", slug, f"success={success} score={meta.score:.1f}")
            # Auto-deprecate when the score collapses.
            if meta.score < DEPRECATE_THRESHOLD:
                deprecate_skill(slug, reason="score below threshold")
            return meta.score
    except Exception:
        return None


def apply_decay() -> int:
    """Apply time-based decay to all non-deprecated skills.

    Skills unused for a while lose DECAY_PER_WEEK points per week of disuse.
    Returns the number of skills touched.
    """
    touched = 0
    try:
        base = _learned_dir()
        if base is None:
            return 0
        now = time.time()
        for child in base.iterdir():
            # Skip dot-dirs and symlinks (mirrors _iter_all_skills).
            if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                continue
            meta = get_meta(child.name)
            if meta is None or meta.deprecated or meta.last_used_ts <= 0:
                continue
            weeks = (now - meta.last_used_ts) / (7 * 24 * 3600)
            if weeks < 1:
                continue
            # Cap per-run decay to 2 weeks' worth. Without this, a single
            # clock jump forward (NTP correction after VM clock skew) would
            # apply years of decay at once and mass-deprecate healthy skills.
            weeks = min(weeks, 2.0)
            loss = min(meta.score, weeks * DECAY_PER_WEEK)
            if loss > 0.5:  # avoid pointless writes for tiny decay
                meta.score = max(0.0, meta.score - loss)
                meta.updated_ts = now
                save_meta(meta)
                _log("decay", meta.slug, f"-{loss:.1f} score={meta.score:.1f}")
                touched += 1
                if meta.score < DEPRECATE_THRESHOLD:
                    deprecate_skill(meta.slug, reason="decayed below threshold")
    except Exception:
        pass
    return touched


def deprecate_skill(slug: str, reason: str = "") -> bool:
    """Mark a skill deprecated (excluded from retrieval, kept on disk)."""
    try:
        meta = get_meta(slug)
        if meta is None:
            return False
        meta.deprecated = True
        meta.updated_ts = time.time()
        if not save_meta(meta):
            return False
        _log("deprecate", slug, reason)
        return True
    except Exception:
        return False


def revive_skill(slug: str) -> bool:
    """Bring a deprecated skill back with a reset score."""
    try:
        meta = get_meta(slug)
        if meta is None or not meta.deprecated:
            return False
        meta.deprecated = False
        meta.score = INITIAL_SCORE
        meta.updated_ts = time.time()
        if not save_meta(meta):
            return False
        _log("revive", slug, f"score reset to {INITIAL_SCORE}")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Semantic retrieval
# ---------------------------------------------------------------------------


def _keyword_overlap(a: str, b: str) -> float:
    """Fallback similarity when embeddings are unavailable.

    Uses query coverage: what fraction of the query's distinctive words
    appear in the candidate. This handles short queries against long docs,
    where Dice/cosine both collapse (e.g. "deploy app" vs a 200-word skill).
    """
    try:
        wa = set(re.findall(r"[a-z0-9]{3,}", a.lower()))
        wb = set(re.findall(r"[a-z0-9]{3,}", b.lower()))
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa)
    except Exception:
        return 0.0


def find_relevant(query: str, top_k: int = 3, min_score: float = 0.35) -> list[dict]:
    """Find skills relevant to a task description.

    Scans all skill directories (private + learned). Uses embedding cosine
    similarity when available, keyword overlap otherwise. Deprecated skills
    are excluded. Returns up to top_k dicts with slug, name, skill_score,
    similarity, rank.
    """
    results: list[dict] = []
    try:
        if not query or not query.strip():
            return results
        q_emb, _ = _embed_text(query)
        use_emb = q_emb is not None
        if use_emb:
            from zeline import embeddings as _emb

        for slug, _ in _iter_all_skills():
            meta = get_meta(slug)
            if meta is not None and meta.deprecated:
                continue
            text = _read_skill_text(slug)
            if not text:
                continue
            label = meta.name if meta and meta.name else slug
            score = meta.score if meta else INITIAL_SCORE
            if use_emb:
                s_emb = _valid_cached_embedding(meta) if meta else None
                if not s_emb:
                    s_emb, s_model = _embed_text(f"{label} {text[:800]}")
                    if s_emb and meta:
                        meta.embedding = s_emb
                        meta.embedding_model = s_model
                        save_meta(meta)
                sim = _emb.cosine_sim(q_emb, s_emb) if s_emb else 0.0
            else:
                sim = _keyword_overlap(query, f"{label} {text[:800]}")
            if sim >= min_score:
                results.append(
                    {
                        "slug": slug,
                        "name": label,
                        "skill_score": round(score, 1),
                        "similarity": round(float(sim), 3),
                        # Combined rank: relevance weighted by effectiveness.
                        "rank": float(sim) * (0.5 + score / 200.0),
                    }
                )
        results.sort(key=lambda r: r["rank"], reverse=True)
        return results[:top_k]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Conflict detection & skill creation
# ---------------------------------------------------------------------------


def check_duplicate(name: str, description: str) -> dict | None:
    """Check whether a proposed skill duplicates an existing one.

    Scans all skill directories (private + learned). Returns
    {"slug", "similarity", "action": "merge"|"related"} or None.
    """
    try:
        probe = f"{name} {description}"
        p_emb, _ = _embed_text(probe)
        if p_emb:
            from zeline import embeddings as _emb

        best: dict | None = None
        best_sim = 0.0
        for slug, _ in _iter_all_skills():
            text = _read_skill_text(slug)
            if not text:
                continue
            meta = get_meta(slug)
            if meta is not None and meta.deprecated:
                continue
            label = meta.name if meta and meta.name else slug
            candidate = f"{label} {text[:800]}"
            sim = 0.0
            if p_emb:
                s_emb = _valid_cached_embedding(meta) if meta else None
                if not s_emb:
                    s_emb, s_model = _embed_text(candidate)
                    if s_emb and meta:
                        meta.embedding = s_emb
                        meta.embedding_model = s_model
                        save_meta(meta)
                if s_emb:
                    sim = float(_emb.cosine_sim(p_emb, s_emb))
            else:
                # Duplicate detection via keyword fallback.
                # Uses query coverage (handles short probe vs long doc), but
                # requires a minimum probe length to avoid false positives on
                # short skill names ("backup" vs "backup-prod" = 1.0 coverage).
                probe_words = set(re.findall(r"[a-z0-9]{3,}", probe.lower()))
                if len(probe_words) >= 4:
                    sim = _keyword_overlap(probe, candidate)
                else:
                    # Short probe: fall back to exact slug/name match only.
                    # This catches "deploy" vs existing "deploy" without the
                    # false positives of query coverage on 1-2 word probes.
                    try:
                        from zeline import learning as _learning

                        probe_slug = _learning._slugify(name)
                    except Exception:
                        probe_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                    sim = 1.0 if probe_slug and probe_slug == slug else 0.0
            if sim > best_sim:
                best_sim = sim
                best = {"slug": slug, "name": label}
        if best is None:
            return None
        if best_sim >= DUPLICATE_THRESHOLD:
            return {**best, "similarity": round(best_sim, 3), "action": "merge"}
        if best_sim >= RELATED_THRESHOLD:
            return {**best, "similarity": round(best_sim, 3), "action": "related"}
        return None
    except Exception:
        return None


def register_skill(
    name: str,
    description: str,
    content: str,
    tags: list[str] | None = None,
) -> dict:
    """Register a new learned skill through the SkillForge pipeline.

    Steps: duplicate check → save via learning.save_learned_skill →
    attach SkillMeta (v1, INITIAL_SCORE, embedding). On near-duplicate,
    merges into the existing skill instead (version bump).

    Returns {"slug", "merged": bool, "duplicate_of": slug|None}.
    """
    result: dict = {"slug": "", "merged": False, "duplicate_of": None}
    try:
        from zeline import learning as _learning

        dup = check_duplicate(name, description)
        if dup and dup["action"] == "merge":
            # Merge: append as an update to the existing skill, bump version.
            # SECURITY + SAFETY: auto-merge is restricted to learned/ only.
            # private/ holds operator-curated skills — merging into them
            # autonomously would bypass the checkpointed manage_skill patch
            # path. For private/ duplicates, fall through to create-new
            # (caller is told it's related via duplicate_of).
            slug = dup["slug"]
            target_dir = _skill_dir(slug)  # learned/ only
            meta = get_meta(slug)
            if target_dir is not None and target_dir.is_dir() and not target_dir.is_symlink():
                # Keep previous content for rollback (FULL text, no truncation —
                # truncated snapshots would destroy data on rollback).
                prev = _read_skill_text_full(slug)
                write_ok = False
                try:
                    md = target_dir / "SKILL.md"
                    if md.is_symlink():
                        raise ValueError("refusing to write through symlink")
                    if md.is_file():
                        txt = md.read_text(encoding="utf-8")
                        md.write_text(
                            txt.rstrip() + f"\n\n## Update ({time.strftime('%Y-%m-%d')})\n\n{content.strip()[:2000]}\n",
                            encoding="utf-8",
                        )
                        write_ok = True
                    else:
                        _learning.improve_learned_skill(
                            slug, f"Merged corroborating experience:\n\n{content.strip()[:2000]}"
                        )
                        write_ok = True
                except Exception:
                    write_ok = False
                if write_ok:
                    if meta is not None:
                        meta.previous_content = prev
                        meta.version += 1
                        meta.updated_ts = time.time()
                        # Content changed → cached embedding is stale; clear it.
                        meta.embedding = None
                        meta.embedding_model = ""
                        # Small score bonus for corroborated knowledge, but capped:
                        # merges alone can't push a skill to 100 without real
                        # successful uses (prevents score inflation via merge loops).
                        if meta.score < 70.0:
                            meta.score = min(70.0, meta.score + 2.0)
                        if not save_meta(meta):
                            # Meta save failed — still report merge success since
                            # the content WAS written; the score bonus is lost.
                            pass
                    _log("merge", slug, f"corroborated experience merged")
                    result.update({"slug": slug, "merged": True, "duplicate_of": slug})
                    return result
                _log("merge-failed", slug, "write failed, creating new")
            else:
                # Target not in learned/ (private/ or missing) — do not
                # auto-merge. Report as related so the caller knows.
                _log("merge-skipped", slug, "target not in learned/, creating new")
                result.update({"slug": "", "merged": False, "duplicate_of": slug})

        path = _learning.save_learned_skill(name, description, content)
        slug = Path(path).name
        meta = SkillMeta(
            slug=slug,
            name=name[:200],
            tags=list(tags or []),
        )
        emb, emb_model = _embed_text(f"{name} {description} {content[:800]}")
        if emb:
            meta.embedding = emb
            meta.embedding_model = emb_model
        save_meta(meta)
        _log("register", slug, f"v1 score={INITIAL_SCORE}")
        if dup and dup["action"] == "related":
            _log("related", slug, f"related to {dup['slug']} sim={dup['similarity']}")
        result.update({"slug": slug, "duplicate_of": (dup or {}).get("slug")})
        return result
    except Exception:
        return result


def rollback_skill(slug: str) -> bool:
    """Restore a skill's previous content version. Returns success."""
    try:
        meta = get_meta(slug)
        if meta is None or not meta.previous_content or meta.version < 2:
            return False
        d = _find_skill_dir_anywhere(slug) or _skill_dir(slug)
        if d is None or d.is_symlink():
            return False
        md = d / "SKILL.md"
        if md.is_symlink():
            return False
        if md.is_file():
            current = md.read_text(encoding="utf-8")
            md.write_text(meta.previous_content, encoding="utf-8")
            # Store FULL current content for potential re-rollback (no truncation —
            # truncated snapshots cause data loss on large skills).
            meta.previous_content = current
            meta.version -= 1
            meta.updated_ts = time.time()
            # Content restored → embedding stale.
            meta.embedding = None
            meta.embedding_model = ""
            save_meta(meta)
            _log("rollback", slug, f"restored v{meta.version}")
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def skill_report() -> dict:
    """Summary of the skill library: counts, avg score, top/bottom skills."""
    report: dict = {
        "total": 0,
        "active": 0,
        "deprecated": 0,
        "avg_score": 0.0,
        "top": [],
        "weak": [],
    }
    try:
        metas: list[SkillMeta] = []
        seen_slugs: set[str] = set()
        unscored = 0
        for slug, _ in _iter_all_skills():
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            meta = get_meta(slug)
            if meta is None:
                # Private/ skills have no SkillMeta (scoring is learned/-only).
                # Count them separately; don't fabricate scores that would
                # pollute avg_score/top/weak.
                unscored += 1
                continue
            metas.append(meta)
        report["total"] = len(metas) + unscored
        report["unscored"] = unscored
        active = [m for m in metas if not m.deprecated]
        report["active"] = len(active)
        report["deprecated"] = len(metas) - len(active)
        if active:
            report["avg_score"] = round(
                sum(m.score for m in active) / len(active), 1
            )
            by_score = sorted(active, key=lambda m: m.score, reverse=True)
            report["top"] = [
                {"slug": m.slug, "name": m.name, "score": round(m.score, 1)}
                for m in by_score[:5]
            ]
            report["weak"] = [
                {"slug": m.slug, "name": m.name, "score": round(m.score, 1)}
                for m in sorted(active, key=lambda m: m.score)[:5]
            ]
        return report
    except Exception:
        return report
