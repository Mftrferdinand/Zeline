#!/usr/bin/env python3
"""
tools/fuzzy_match.py — Fuzzy String Matching & Entity Resolution  (v1.0)

Agent sering harus nyocokin string yang mirip tapi gak identik: nama file,
skill, entitas dari user ("wa" vs "whatsapp"), dedup judul. Tool ini kasih
similarity skor (Dice coefficient over bigrams) + matcher terbaik dari
daftar kandidat, tanpa dependency (difflib terlalu lambat untuk daftar
panjang dan skornya kurang cocok buat typo pendek).

API:
- similarity(a, b) -> float        0.0-1.0 bigram Dice
- best_match(query, choices, threshold) -> (choice, score) | (None, 0.0)
- match_all(query, choices, threshold) -> list[(choice, score)] sorted desc
- dedupe(items, threshold) -> list[list]  cluster item yang mirip
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(s: str) -> str:
    return " ".join(_WORD_RE.findall((s or "").lower()))


def _bigrams(s: str) -> Counter[str]:
    if len(s) < 2:
        return Counter([s]) if s else Counter()
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


def similarity(a: str, b: str) -> float:
    """Dice coefficient over character bigrams of normalized strings."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 1.0 if na == nb else 0.0
    if na == nb:
        return 1.0
    ba, bb = _bigrams(na), _bigrams(nb)
    overlap = sum((ba & bb).values())
    total = sum(ba.values()) + sum(bb.values())
    if total == 0:
        return 0.0
    return 2 * overlap / total


def best_match(query: str, choices: Iterable[str],
               threshold: float = 0.6) -> tuple[str | None, float]:
    """The highest-scoring choice, or (None, 0.0) under the threshold."""
    best: str | None = None
    best_score = 0.0
    for choice in choices:
        score = similarity(query, choice)
        if score > best_score:
            best, best_score = choice, score
    if best is None or best_score < threshold:
        return None, 0.0
    return best, round(best_score, 4)


def match_all(query: str, choices: Iterable[str],
              threshold: float = 0.6) -> list[tuple[str, float]]:
    """All choices scoring at/above the threshold, best first."""
    scored = [(c, round(similarity(query, c), 4)) for c in choices]
    scored = [(c, s) for c, s in scored if s >= threshold]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def dedupe(items: Iterable[str], threshold: float = 0.85) -> list[list[str]]:
    """Greedy clustering: first item seeds a cluster; similar items join it."""
    clusters: list[list[str]] = []
    for item in items:
        for cluster in clusters:
            if similarity(item, cluster[0]) >= threshold:
                cluster.append(item)
                break
        else:
            clusters.append([item])
    return clusters


def main() -> int:
    """CLI: python3 -m zeline.zenith_tools.scripts.fuzzy_match 'query' 'c1' 'c2' ..."""
    import json
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    query, choices = sys.argv[1], sys.argv[2:]
    best = best_match(query, choices)
    print(json.dumps({
        "best": {"choice": best[0], "score": best[1]},
        "all": [{"choice": c, "score": s} for c, s in match_all(query, choices)],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
