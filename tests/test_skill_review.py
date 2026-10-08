"""Test zeline.skill_review — mesin review periodik self-improving skills.

Catatan: ``zeline.skill_telemetry`` dibuat worker lain secara paralel dan
belum ada di repo saat test ini ditulis. Kontraknya diasumsikan persis
seperti di task (record_load/record_outcome/stats/all_stats/owner_identity
+ global_stats untuk agregat lintas-identitas), jadi test memakai FAKE
yang mengimplementasikan kontrak itu — modul ``skill_review`` hanya memakai
``stats(name, identity)`` dan ``global_stats(name)`` lewat import malas,
sehingga fake cukup diinjeksikan ke ``sys.modules`` sebelum import.

Semua path diarahkan ke tmp_path: TIDAK ADA yang menyentuh ~/.zeline asli.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fake telemetry: implementasi kontrak zeline.skill_telemetry (modul asli
# dibuat paralel oleh worker lain — lihat docstring modul test).
# ---------------------------------------------------------------------------
class _FakeTelemetry:
    def __init__(self):
        self._stats: dict[tuple[str, str], dict] = {}

    def set(self, identity, name, loads=0, successes=0, failures=0,
            consecutive_failures=0):
        outcomes = successes + failures
        self._stats[(identity, name)] = {
            "loads": loads,
            "successes": successes,
            "failures": failures,
            "consecutive_failures": consecutive_failures,
            "success_rate": (successes / outcomes) if outcomes else 0.0,
        }

    def record_load(self, skill_name, identity):  # pragma: no cover
        raise NotImplementedError

    def record_outcome(self, skill_name, identity, ok, duration_s=0.0,
                       error_kind=""):  # pragma: no cover
        raise NotImplementedError

    def stats(self, name, identity):
        return dict(
            self._stats.get(
                (identity, name),
                {
                    "loads": 0,
                    "successes": 0,
                    "failures": 0,
                    "consecutive_failures": 0,
                    "success_rate": 0.0,
                },
            )
        )

    def all_stats(self, identity):
        return {
            name: dict(s)
            for (ident, name), s in self._stats.items()
            if ident == identity
        }

    def owner_identity(self, identity):
        return identity

    def global_stats(self, name):
        """Agregat semua identitas — implementasi kontrak baru (MAJOR 3)."""
        loads = successes = failures = 0
        max_cons = 0
        for (ident, sname), s in self._stats.items():
            if sname != name:
                continue
            loads += s["loads"]
            successes += s["successes"]
            failures += s["failures"]
            max_cons = max(max_cons, s["consecutive_failures"])
        outcomes = successes + failures
        return {
            "loads": loads,
            "successes": successes,
            "failures": failures,
            "consecutive_failures": max_cons,
            "success_rate": (successes / outcomes) if outcomes else 0.0,
        }


_FAKE_TELEMETRY = _FakeTelemetry()

from zeline import curator as curator_mod  # noqa: E402
from zeline import skill_review as review  # noqa: E402
from zeline import skills as skills_mod  # noqa: E402

IDENTITY = "test-review-identity"


@pytest.fixture(autouse=True)
def _fake_telemetry_module(monkeypatch):
    """Injeksi fake ke sys.modules per-test; otomatis dibersihkan setelahnya.

    Penting: modul telemetri ASLI (dibuat worker paralel) tidak boleh
    terbayang-bayangi fake ini di luar test file ini.
    """
    monkeypatch.setitem(sys.modules, "zeline.skill_telemetry", _FAKE_TELEMETRY)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """private/ + public/ di tmp; konstanta zeline.skills dipatch ke sana."""
    private = tmp_path / "private"
    public = tmp_path / "public"
    private.mkdir()
    public.mkdir()
    monkeypatch.setattr(skills_mod, "PRIVATE_SKILLS_DIR", private)
    monkeypatch.setattr(skills_mod, "PUBLIC_SKILLS_DIR", public)
    _FAKE_TELEMETRY._stats.clear()
    return {
        "root": tmp_path,
        "private": private,
        "public": public,
        "ledger": tmp_path / "review.jsonl",
        "curator_ledger": tmp_path / "curator.jsonl",
        "priority_dir": tmp_path / "priority",
    }


def _make_skill(root: Path, name: str, description: str = "Skill test.",
                days_old: float = 0.0) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nIsi skill.\n",
        encoding="utf-8",
    )
    if days_old:
        old = time.time() - days_old * 86400
        for p in (d, d / "SKILL.md"):
            os.utime(p, (old, old))
    return d


def _review(dirs, **kwargs):
    kwargs.setdefault("ledger_path", dirs["ledger"])
    kwargs.setdefault("curator_ledger_path", dirs["curator_ledger"])
    kwargs.setdefault("priority_dir", dirs["priority_dir"])
    return review.review_skills(IDENTITY, **kwargs)


def _snapshot(root: Path) -> dict:
    """Potret rekursif: path relatif -> (is_dir, mtime_ns, size)."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            snap[str(p.relative_to(root))] = (
                p.is_dir(),
                st.st_mtime_ns,
                0 if p.is_dir() else st.st_size,
            )
    return snap


# ---------------------------------------------------------------------------
# promote / demote
# ---------------------------------------------------------------------------
def test_promote_applies_and_is_not_repeated(dirs):
    _make_skill(dirs["private"], "bagus", "Skill yang sangat membantu.")
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=4, failures=1)

    plan = _review(dirs, apply=False)
    item = next(i for i in plan if i["skill"] == "bagus")
    assert item["action"] == "promote"
    assert set(item) >= {"skill", "action", "reason", "details"}

    plan = _review(dirs, apply=True)
    assert review.get_priority("bagus", IDENTITY, dirs["priority_dir"]) == 1

    # Sudah +1: review berikutnya tidak mempromote ulang.
    plan2 = _review(dirs, apply=False)
    assert not [i for i in plan2 if i["skill"] == "bagus" and i["action"] == "promote"]


def test_demote_keeps_skill_usable(dirs):
    _make_skill(dirs["private"], "jelek", "Skill yang sering gagal.")
    _FAKE_TELEMETRY.set(IDENTITY, "jelek", loads=3, successes=1, failures=2)

    plan = _review(dirs, apply=True)
    item = next(i for i in plan if i["skill"] == "jelek")
    assert item["action"] == "demote"
    assert review.get_priority("jelek", IDENTITY, dirs["priority_dir"]) == -1
    # Demote BUKAN arsip: skill tetap ada, tidak dipindah ke .archive.
    assert (dirs["private"] / "jelek" / "SKILL.md").is_file()
    assert not (dirs["private"] / ".archive").exists()


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------
def test_archive_unused_stale_skill_and_restore(dirs):
    _make_skill(dirs["private"], "tua", "Skill lama tak terpakai.", days_old=100)
    # Telemetri punya data (untuk skill lain) -> pengaman "telemetri kosong"
    # tidak aktif; skill "tua" sendiri tetap loads==0.
    _make_skill(dirs["private"], "lain", "Skill lain.")
    _FAKE_TELEMETRY.set(IDENTITY, "lain", loads=2, successes=2, failures=0)

    plan = _review(dirs, apply=True)
    item = next(i for i in plan if i["skill"] == "tua")
    assert item["action"] == "archive"
    assert not (dirs["private"] / "tua").exists()
    assert (dirs["private"] / ".archive").is_dir()

    # Recoverable: restore() mengembalikan skill.
    dst = curator_mod.restore(
        "tua", skills_dir=dirs["private"], ledger_path=dirs["curator_ledger"]
    )
    assert (dirs["private"] / "tua" / "SKILL.md").is_file()
    assert dst == dirs["private"] / "tua"


def test_archive_consecutive_failures(dirs):
    _make_skill(dirs["private"], "rusak", "Skill yang selalu gagal.")
    _FAKE_TELEMETRY.set(
        IDENTITY, "rusak", loads=5, successes=0, failures=5, consecutive_failures=5
    )

    plan = _review(dirs, apply=True)
    item = next(i for i in plan if i["skill"] == "rusak")
    assert item["action"] == "archive"
    assert not (dirs["private"] / "rusak").exists()


def test_no_unused_archive_when_telemetry_empty(dirs):
    """Telemetri kosong (baru dipasang) -> loads==0 bukan bukti; tidak arsip."""
    _make_skill(dirs["private"], "lama", "Skill lama.", days_old=100)
    # Tidak ada data telemetri sama sekali untuk identity ini.
    assert _FAKE_TELEMETRY.all_stats(IDENTITY) == {}

    plan = _review(dirs, apply=True)
    assert not [i for i in plan if i["skill"] == "lama" and i["action"] == "archive"]
    assert (dirs["private"] / "lama" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# dry-run murni
# ---------------------------------------------------------------------------
def test_dry_run_writes_nothing(dirs):
    _make_skill(dirs["private"], "bagus", "Skill yang sangat membantu.")
    _make_skill(dirs["private"], "tua", "Skill lama tak terpakai.", days_old=100)
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=4, failures=1)

    before = _snapshot(dirs["root"])
    assert not dirs["ledger"].exists()
    assert not dirs["curator_ledger"].exists()
    assert not dirs["priority_dir"].exists()

    plan = _review(dirs, apply=False)
    assert any(i["action"] == "promote" for i in plan)
    assert any(i["action"] == "archive" for i in plan)

    after = _snapshot(dirs["root"])
    assert before == after, "dry-run mengubah file!"
    assert not dirs["ledger"].exists()
    assert not dirs["curator_ledger"].exists()
    assert not dirs["priority_dir"].exists()


# ---------------------------------------------------------------------------
# skill public tidak boleh disentuh
# ---------------------------------------------------------------------------
def test_public_skill_never_archived(dirs):
    _make_skill(
        dirs["public"], "bawaan", "Skill bawaan public yang basi.", days_old=200
    )
    # Bahkan dengan statistik promote-worthy, skill public tidak dimutasi.
    _FAKE_TELEMETRY.set(IDENTITY, "bawaan", loads=10, successes=9, failures=1)

    plan = _review(dirs, apply=True, skills_dir=dirs["public"])
    for item in plan:
        assert item["action"] not in ("archive", "promote", "demote"), item
    assert (dirs["public"] / "bawaan" / "SKILL.md").is_file()
    assert review.get_priority("bawaan", IDENTITY, dirs["priority_dir"]) == 0


# ---------------------------------------------------------------------------
# overlap hanya dilaporkan
# ---------------------------------------------------------------------------
def test_report_overlap_no_auto_action(dirs):
    prefix = "Helper untuk otomasi tugas harian yang berulang-ulang "
    _make_skill(dirs["private"], "auto-a", prefix + "versi pertama.")
    _make_skill(dirs["private"], "auto-b", prefix + "versi kedua.")

    plan = _review(dirs, apply=True)
    overlaps = [i for i in plan if i["action"] == "report_overlap"]
    assert {i["skill"] for i in overlaps} == {"auto-a", "auto-b"}
    # Tidak ada yang ter-archive karena overlap.
    assert not [i for i in plan if i["action"] == "archive"]
    assert (dirs["private"] / "auto-a" / "SKILL.md").is_file()
    assert (dirs["private"] / "auto-b" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------
def test_rollback_priority(dirs):
    _make_skill(dirs["private"], "bagus", "Skill yang sangat membantu.")
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=4, failures=1)
    _review(dirs, apply=True)
    assert review.get_priority("bagus", IDENTITY, dirs["priority_dir"]) == 1

    log = review.get_change_log(IDENTITY, dirs["ledger"])
    change_id = next(
        r["id"] for r in log if r["skill"] == "bagus" and r["action"] == "set_priority"
    )
    msg = review.rollback_change(
        change_id,
        IDENTITY,
        skills_dir=dirs["private"],
        ledger_path=dirs["ledger"],
        curator_ledger_path=dirs["curator_ledger"],
        priority_dir=dirs["priority_dir"],
    )
    assert "bagus" in msg
    assert review.get_priority("bagus", IDENTITY, dirs["priority_dir"]) == 0


def test_rollback_archive(dirs):
    _make_skill(dirs["private"], "tua", "Skill lama tak terpakai.", days_old=100)
    _make_skill(dirs["private"], "lain", "Skill lain.")
    _FAKE_TELEMETRY.set(IDENTITY, "lain", loads=2, successes=2, failures=0)
    _review(dirs, apply=True)
    assert not (dirs["private"] / "tua").exists()

    log = review.get_change_log(IDENTITY, dirs["ledger"])
    change_id = next(
        r["id"] for r in log if r["skill"] == "tua" and r["action"] == "archive"
    )
    msg = review.rollback_change(
        change_id,
        IDENTITY,
        skills_dir=dirs["private"],
        ledger_path=dirs["ledger"],
        curator_ledger_path=dirs["curator_ledger"],
        priority_dir=dirs["priority_dir"],
    )
    assert "tua" in msg
    assert (dirs["private"] / "tua" / "SKILL.md").is_file()


def test_rollback_unknown_id_raises(dirs):
    with pytest.raises(curator_mod.CuratorError, match="tidak dikenal"):
        review.rollback_change(
            "id-yang-tidak-ada",
            IDENTITY,
            ledger_path=dirs["ledger"],
            priority_dir=dirs["priority_dir"],
        )


# ---------------------------------------------------------------------------
# ambang batas: tepat di bawah threshold tidak ter-trigger
# ---------------------------------------------------------------------------
def test_threshold_boundaries(dirs):
    # 4 loads (< 5): tidak dipromote walau success_rate 100%.
    _make_skill(dirs["private"], "hampir", "Skill hampir cukup data.")
    _FAKE_TELEMETRY.set(IDENTITY, "hampir", loads=4, successes=4, failures=0)
    # success_rate tepat 0.4: tidak didemote (syaratnya < 0.4).
    _make_skill(dirs["private"], "pas", "Skill pas di batas.")
    _FAKE_TELEMETRY.set(IDENTITY, "pas", loads=5, successes=2, failures=3)
    # 4 gagal beruntun (< 5): tidak diarsip.
    _make_skill(dirs["private"], "goyah", "Skill kadang gagal.")
    _FAKE_TELEMETRY.set(
        IDENTITY, "goyah", loads=4, successes=0, failures=4, consecutive_failures=4
    )

    plan = _review(dirs, apply=False)
    by_skill = {i["skill"]: i["action"] for i in plan}
    assert by_skill.get("hampir") != "promote"
    assert by_skill.get("pas") != "demote"
    assert by_skill.get("goyah") != "archive"


# ---------------------------------------------------------------------------
# priority store
# ---------------------------------------------------------------------------
def test_set_priority_validates_level(dirs):
    with pytest.raises(ValueError, match="tidak valid"):
        review.set_priority(
            "x", IDENTITY, 2, "alasan", priority_dir=dirs["priority_dir"]
        )
    with pytest.raises(ValueError, match="tidak valid"):
        review.set_priority(
            "x", IDENTITY, -2, "alasan", priority_dir=dirs["priority_dir"]
        )


def test_set_priority_returns_change_record(dirs):
    rec = review.set_priority(
        "s1",
        IDENTITY,
        1,
        "bagus",
        priority_dir=dirs["priority_dir"],
        ledger_path=dirs["ledger"],
    )
    assert rec["previous_level"] == 0
    assert rec["new_level"] == 1
    assert rec["reason"] == "bagus"
    assert rec["id"]
    # Tercatat di change log dengan previous_state.
    log = review.get_change_log(IDENTITY, dirs["ledger"])
    entry = next(r for r in log if r["id"] == rec["id"])
    assert entry["previous_state"]["previous_level"] == 0
    assert entry["skill"] == "s1"


def test_ranked_order_stable(dirs):
    review.set_priority("b", IDENTITY, 1, "t", priority_dir=dirs["priority_dir"])
    review.set_priority("c", IDENTITY, -1, "t", priority_dir=dirs["priority_dir"])
    assert review.ranked_order(
        ["a", "b", "c", "d"], IDENTITY, dirs["priority_dir"]
    ) == ["b", "a", "d", "c"]
    # Default 0 untuk skill yang belum diset.
    assert review.get_priority("belum-ada", IDENTITY, dirs["priority_dir"]) == 0


def test_priority_file_is_0600(dirs):
    review.set_priority("s1", IDENTITY, 1, "t", priority_dir=dirs["priority_dir"])
    path = dirs["priority_dir"] / f"{review._identity_hash(IDENTITY)}.json"
    assert path.stat().st_mode & 0o777 == 0o600


def test_change_log_records_have_required_fields(dirs):
    _make_skill(dirs["private"], "bagus", "Skill yang sangat membantu.")
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=4, failures=1)
    _review(dirs, apply=True)
    log = review.get_change_log(IDENTITY, dirs["ledger"])
    assert log, "change log kosong setelah apply"
    for record in log:
        assert set(record) >= {
            "id",
            "ts",
            "action",
            "skill",
            "reason",
            "previous_state",
        }, record


# ---------------------------------------------------------------------------
# Lintas-identitas (MAJOR 3): direktori skill dipakai bersama — arsip butuh
# bukti global, bukan hanya dari satu identitas.
# ---------------------------------------------------------------------------
IDENTITY_B = "test-review-identity-b"


def test_no_unused_archive_when_other_identity_uses_skill(dirs):
    _make_skill(
        dirs["private"], "dipakai-b", "Dipakai identitas lain.", days_old=100
    )
    _make_skill(dirs["private"], "lain", "Skill lain.")
    _FAKE_TELEMETRY.set(IDENTITY, "lain", loads=2, successes=2, failures=0)
    # Identitas B memakai skill itu 4x — review identitas A tidak boleh
    # mengarsipkannya walau A sendiri tak pernah memakainya.
    _FAKE_TELEMETRY.set(IDENTITY_B, "dipakai-b", loads=4, successes=4, failures=0)

    plan = _review(dirs, apply=False)
    assert not any(
        i["skill"] == "dipakai-b" and i["action"] == "archive" for i in plan
    ), plan
    assert (dirs["private"] / "dipakai-b").is_dir()


def test_unused_archive_applies_when_globally_unused(dirs):
    _make_skill(
        dirs["private"], "tua2", "Tak dipakai siapa pun.", days_old=100
    )
    _make_skill(dirs["private"], "lain", "Skill lain.")
    _FAKE_TELEMETRY.set(IDENTITY, "lain", loads=2, successes=2, failures=0)
    _FAKE_TELEMETRY.set(IDENTITY_B, "lain-b", loads=1, successes=1, failures=0)

    plan = _review(dirs, apply=True)
    item = next(i for i in plan if i["skill"] == "tua2")
    assert item["action"] == "archive"
    assert not (dirs["private"] / "tua2").exists()


def test_no_failure_archive_when_globally_healthy(dirs):
    _make_skill(dirs["private"], "rewel", "Gagal di A, sukses di B.")
    _FAKE_TELEMETRY.set(
        IDENTITY, "rewel", loads=5, successes=0, failures=5,
        consecutive_failures=5,
    )
    # Identitas B memakainya dengan sukses — kegagalan ini spesifik konteks
    # A: demote per-identitas, bukan arsip untuk semua orang.
    _FAKE_TELEMETRY.set(IDENTITY_B, "rewel", loads=10, successes=10, failures=0)

    plan = _review(dirs, apply=False)
    item = next(i for i in plan if i["skill"] == "rewel")
    assert item["action"] == "demote", item
    assert (dirs["private"] / "rewel").is_dir()


def test_failure_archive_applies_when_globally_broken(dirs):
    _make_skill(dirs["private"], "rusak2", "Gagal di mana-mana.")
    _FAKE_TELEMETRY.set(
        IDENTITY, "rusak2", loads=5, successes=0, failures=5,
        consecutive_failures=5,
    )
    _FAKE_TELEMETRY.set(
        IDENTITY_B, "rusak2", loads=3, successes=0, failures=3,
        consecutive_failures=3,
    )

    plan = _review(dirs, apply=True)
    item = next(i for i in plan if i["skill"] == "rusak2")
    assert item["action"] == "archive"
    assert not (dirs["private"] / "rusak2").exists()


# ---------------------------------------------------------------------------
# apply_plan: satu-satunya jalur eksekusi (anti-TOCTOU)
# ---------------------------------------------------------------------------
def test_apply_plan_rejects_unknown_action(dirs):
    with pytest.raises(curator_mod.CuratorError):
        review.apply_plan(
            IDENTITY,
            [{"skill": "x", "action": "menjadi-kaya", "reason": "???"}],
            ledger_path=dirs["ledger"],
            priority_dir=dirs["priority_dir"],
        )


def test_apply_plan_idempotent(dirs):
    _make_skill(dirs["private"], "bagus", "Skill bagus.")
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=5, failures=0)
    plan = _review(dirs, apply=False)
    item = next(i for i in plan if i["skill"] == "bagus")
    assert item["action"] == "promote"
    review.apply_plan(
        IDENTITY, plan,
        ledger_path=dirs["ledger"], priority_dir=dirs["priority_dir"],
    )
    # Panggil ulang dengan rencana yang sama: dilewati, tidak meledak.
    review.apply_plan(
        IDENTITY, plan,
        ledger_path=dirs["ledger"], priority_dir=dirs["priority_dir"],
    )
    assert review.get_priority("bagus", IDENTITY, dirs["priority_dir"]) == 1


def test_apply_plan_validates_whole_plan_before_applying(dirs):
    _make_skill(dirs["private"], "bagus", "Skill bagus.")
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=5, failures=0)
    plan = _review(dirs, apply=False)
    assert any(i["action"] == "promote" for i in plan)
    bad_plan = plan + [{"skill": "x", "action": "jahat", "reason": "?"}]
    with pytest.raises(curator_mod.CuratorError):
        review.apply_plan(
            IDENTITY, bad_plan,
            ledger_path=dirs["ledger"], priority_dir=dirs["priority_dir"],
        )
    # Satu item asing -> seluruh rencana dibatalkan, tidak setengah jalan.
    assert review.get_priority("bagus", IDENTITY, dirs["priority_dir"]) == 0


# ---------------------------------------------------------------------------
# Temuan audit MINOR-A (2026-10-07)
# ---------------------------------------------------------------------------

def test_no_unused_archive_when_global_outcomes_exist_but_no_loads(dirs):
    """Arsip 'tak dipakai' butuh bukti global NOL loads DAN NOL outcomes.

    Skill basi yang tidak pernah di-load tapi punya outcome tercatat (mis.
    outcome tercatat tanpa record_load) pernah disentuh — tidak boleh
    diarsip sebagai 'tidak pernah dipakai'.
    """
    _make_skill(dirs["private"], "aneh", "Skill basi ber-outcome.", days_old=100)
    _make_skill(dirs["private"], "lain", "Skill lain.")
    _FAKE_TELEMETRY.set(IDENTITY, "lain", loads=2, successes=2, failures=0)
    # Nol loads global, tapi ada outcome global untuk "aneh".
    _FAKE_TELEMETRY.set(IDENTITY, "aneh", loads=0, successes=1, failures=0)

    plan = _review(dirs, apply=False)
    assert not [
        i for i in plan if i["skill"] == "aneh" and i["action"] == "archive"
    ], plan
    assert (dirs["private"] / "aneh").is_dir()


def test_apply_plan_compensates_on_midway_failure(dirs):
    """Satu item gagal di tengah -> aksi yang sudah berjalan dibatalkan.

    Rencana: promote 'bagus' (sukses) + archive 'hantu' (gagal — skill tidak
    ada). Hasil: CuratorError, prioritas 'bagus' kembali ke 0, tidak ada
    skill yang terarsip, flag applied item yang dikompensasi direset.
    """
    _make_skill(dirs["private"], "bagus", "Skill bagus.")
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=5, failures=0)
    plan = _review(dirs, apply=False)
    promote_item = next(i for i in plan if i["action"] == "promote")
    bad_plan = [
        promote_item,
        {"skill": "hantu", "action": "archive", "reason": "tidak ada"},
    ]
    with pytest.raises(curator_mod.CuratorError, match="dibatalkan kembali"):
        review.apply_plan(
            IDENTITY, bad_plan,
            skills_dir=dirs["private"],
            ledger_path=dirs["ledger"],
            curator_ledger_path=dirs["curator_ledger"],
            priority_dir=dirs["priority_dir"],
        )
    # Kompensasi: tidak ada apply setengah jalan.
    assert review.get_priority("bagus", IDENTITY, dirs["priority_dir"]) == 0
    assert (dirs["private"] / "bagus").is_dir()
    # Tidak ada skill yang terarsip (dir .archive boleh ada karena curator
    # mkdir eager — yang penting kosong).
    archived = list((dirs["private"] / ".archive").iterdir())
    assert archived == []
    assert promote_item.get("applied") is False


def test_apply_plan_compensates_archive_via_restore(dirs):
    """Arsip yang sudah berjalan di-restore saat item berikutnya gagal."""
    _make_skill(dirs["private"], "tua", "Skill tua.", days_old=100)
    _make_skill(dirs["private"], "lain", "Skill lain.")
    _FAKE_TELEMETRY.set(IDENTITY, "lain", loads=2, successes=2, failures=0)
    plan = _review(dirs, apply=False)
    archive_item = next(
        i for i in plan if i["skill"] == "tua" and i["action"] == "archive"
    )
    bad_plan = [
        archive_item,
        {"skill": "hantu", "action": "archive", "reason": "tidak ada"},
    ]
    with pytest.raises(curator_mod.CuratorError, match="dibatalkan kembali"):
        review.apply_plan(
            IDENTITY, bad_plan,
            skills_dir=dirs["private"],
            ledger_path=dirs["ledger"],
            curator_ledger_path=dirs["curator_ledger"],
            priority_dir=dirs["priority_dir"],
        )
    # Kompensasi: skill kembali dari arsip ke tempat semula.
    assert (dirs["private"] / "tua" / "SKILL.md").is_file()
    assert archive_item.get("applied") is False


def test_apply_plan_clears_cached_plan_on_success(dirs, monkeypatch):
    """Pasca-apply sukses, plan cache di tool layer dibersihkan."""
    fake_tools = type("FakeTools", (), {})()
    fake_tools._REVIEW_PLAN_CACHE = {IDENTITY: (0.0, [{"skill": "x"}])}
    monkeypatch.setitem(sys.modules, "zeline.tools", fake_tools)

    _make_skill(dirs["private"], "bagus", "Skill bagus.")
    _FAKE_TELEMETRY.set(IDENTITY, "bagus", loads=5, successes=5, failures=0)
    plan = _review(dirs, apply=False)
    review.apply_plan(
        IDENTITY, plan,
        ledger_path=dirs["ledger"], priority_dir=dirs["priority_dir"],
    )
    assert IDENTITY not in fake_tools._REVIEW_PLAN_CACHE


def test_apply_plan_keeps_cache_on_failure(dirs, monkeypatch):
    """Apply yang gagal (validasi) tidak menyentuh plan cache."""
    fake_tools = type("FakeTools", (), {})()
    cached = {IDENTITY: (0.0, [{"skill": "x"}])}
    fake_tools._REVIEW_PLAN_CACHE = cached
    monkeypatch.setitem(sys.modules, "zeline.tools", fake_tools)

    with pytest.raises(curator_mod.CuratorError):
        review.apply_plan(
            IDENTITY,
            [{"skill": "x", "action": "jahat", "reason": "?"}],
            ledger_path=dirs["ledger"], priority_dir=dirs["priority_dir"],
        )
    assert fake_tools._REVIEW_PLAN_CACHE == cached


class _StrippingTelemetry(_FakeTelemetry):
    """Fake dengan owner_identity seperti aslinya (kupas suffix worker)."""

    def owner_identity(self, identity):
        import re
        return re.sub(r"::(wkr|sub)[A-Za-z0-9]*$", "", identity or "") or "cli:local"


def test_priority_store_normalized_to_owner_identity(dirs, monkeypatch):
    """Prioritas milik owner dipakai bersama worker-nya (tidak terfragmentasi)."""
    monkeypatch.setitem(
        sys.modules, "zeline.skill_telemetry", _StrippingTelemetry()
    )
    review.set_priority(
        "s1", "alice::wkr3f8a2b1c", 1, "t", priority_dir=dirs["priority_dir"]
    )
    # Owner dan worker lain melihat prioritas yang sama.
    assert review.get_priority("s1", "alice", dirs["priority_dir"]) == 1
    assert review.get_priority("s1", "alice::wkr999", dirs["priority_dir"]) == 1
    assert review.get_priority("s1", "alice::subxyz", dirs["priority_dir"]) == 1
    # Tepat satu file: milik owner — tidak ada file per worker.
    files = list(dirs["priority_dir"].glob("*.json"))
    assert len(files) == 1
    assert files[0].name == f"{review._identity_hash('alice')}.json"


def test_priority_store_does_not_leak_across_owners(dirs, monkeypatch):
    """Prioritas identitas A tidak bocor ke identitas B."""
    monkeypatch.setitem(
        sys.modules, "zeline.skill_telemetry", _StrippingTelemetry()
    )
    review.set_priority("s1", "alice", 1, "t", priority_dir=dirs["priority_dir"])
    assert review.get_priority("s1", "bob", dirs["priority_dir"]) == 0
    assert review.get_priority("s1", "bob::wkr1", dirs["priority_dir"]) == 0


def test_review_ledger_path_normalized_to_owner_identity(monkeypatch):
    """Ledger review milik owner dipakai bersama worker-nya (tidak terfragmentasi).

    Temuan audit MINOR: ``_review_ledger_path`` tidak di-owner-normalize
    seperti priority store, sehingga review via identitas worker menulis ke
    file mati yang tidak pernah dibaca lagi.
    """
    monkeypatch.setitem(
        sys.modules, "zeline.skill_telemetry", _StrippingTelemetry()
    )
    owner_path = review._review_ledger_path("alice")
    assert review._review_ledger_path("alice::wkr3f8a2b1c") == owner_path
    assert review._review_ledger_path("alice::subxyz") == owner_path
    assert owner_path.name == f"{review._identity_hash('alice')}.jsonl"
    # Konsisten dengan priority store di bawah normalisasi yang sama.
    assert review._priority_path("alice") == review._priority_path(
        "alice::wkr3f8a2b1c"
    )


def test_review_ledger_path_does_not_leak_across_owners(monkeypatch):
    """Ledger review identitas A tidak bocor ke identitas B."""
    monkeypatch.setitem(
        sys.modules, "zeline.skill_telemetry", _StrippingTelemetry()
    )
    assert review._review_ledger_path("bob") != review._review_ledger_path("alice")
    assert review._review_ledger_path("bob::wkr1") != review._review_ledger_path(
        "alice"
    )


def test_review_ledger_explicit_path_not_normalized(tmp_path):
    """ledger_path eksplisit tetap menang (kontrak lama, tidak dinormalisasi)."""
    explicit = tmp_path / "custom.jsonl"
    assert review._review_ledger_path("alice::wkr3f8a2b1c", explicit) == explicit
