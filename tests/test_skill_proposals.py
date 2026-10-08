"""Test zeline.skill_proposals: proposal perbaikan konten skill.

Semua test memakai direktori skill tiruan di tmp_path (monkeypatch atas
zeline.skills + zeline.skill_proposals) — skill asli TIDAK PERNAH disentuh.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from zeline import skill_proposals as sp
from zeline import skills as _skills

IDENTITY = "test:skill-proposals"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Direktori skill + proposal tiruan, terisolasi penuh."""
    skill_root = tmp_path / "skills"
    public = skill_root / "public"
    private = skill_root / "private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    monkeypatch.setattr(_skills, "SKILLS_ROOT", skill_root)
    monkeypatch.setattr(_skills, "PUBLIC_SKILLS_DIR", public)
    monkeypatch.setattr(_skills, "PRIVATE_SKILLS_DIR", private)
    prop_dir = tmp_path / "skill-proposals"
    monkeypatch.setattr(sp, "_proposal_dir", lambda: prop_dir)
    return {"public": public, "private": private, "prop_dir": prop_dir}


def _make_skill(sandbox, name, content, scope="private"):
    unit = sandbox[scope] / name
    unit.mkdir(parents=True, exist_ok=True)
    entry = unit / "SKILL.md"
    entry.write_text(content, encoding="utf-8")
    return entry


SKILL_BODY = """# Demo

Baris lama yang akan diperbaiki.

Bagian lain tidak tersentuh.
"""
OLD = "Baris lama yang akan diperbaiki."
NEW = "Baris baru hasil perbaikan."


def _propose(sandbox, **over):
    kwargs = {
        "skill_name": "demo",
        "identity": IDENTITY,
        "old_text": OLD,
        "new_text": NEW,
        "reason": "typo / fakta basi",
        "title": "Perbaiki baris basi",
    }
    kwargs.update(over)
    return sp.propose_fix(**kwargs)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def test_propose_valid_does_not_touch_skill_file(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    before = entry.read_bytes()
    proposal = _propose(sandbox)
    assert proposal["status"] == "pending"
    assert proposal["skill_name"] == "demo"
    assert proposal["file_path"] == "SKILL.md"
    assert proposal["title"] == "Perbaiki baris basi"
    assert proposal["applied_at"] == ""
    assert proposal["id"].startswith("p-")
    # File skill TIDAK berubah: byte identik sebelum/sesudah propose.
    assert entry.read_bytes() == before
    assert OLD in entry.read_text(encoding="utf-8")
    assert NEW not in entry.read_text(encoding="utf-8")


def test_propose_never_calls_manage_skill(sandbox, monkeypatch):
    """Bukti struktural: jalur propose tidak memanggil manage_skill sama sekali."""
    _make_skill(sandbox, "demo", SKILL_BODY)

    def _boom(*args, **kwargs):
        raise AssertionError("manage_skill terpanggil dari jalur propose!")

    monkeypatch.setattr(sp._skills, "manage_skill", _boom)
    proposal = _propose(sandbox)
    assert proposal["status"] == "pending"


def test_propose_old_text_mismatch(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    with pytest.raises(ValueError, match="cocok PERSIS"):
        _propose(sandbox, old_text="teks yang tidak ada di file")


def test_propose_old_text_not_unique(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY + "\n" + OLD + "\n")
    with pytest.raises(ValueError, match="ditemukan 2 kali"):
        _propose(sandbox)


def test_propose_empty_reason(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    with pytest.raises(ValueError, match="reason tidak boleh kosong"):
        _propose(sandbox, reason="   ")


def test_propose_empty_new_text(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    with pytest.raises(ValueError, match="new_text tidak boleh kosong"):
        _propose(sandbox, new_text="  ")


def test_propose_new_text_same_as_old(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    with pytest.raises(ValueError, match="harus berbeda"):
        _propose(sandbox, new_text=OLD)


@pytest.mark.parametrize("evil", ["../jahat", "..\\jahat", "a/b", "Demo Skill", "DEMO", ""])
def test_propose_rejects_unsafe_names(sandbox, evil):
    _make_skill(sandbox, "demo", SKILL_BODY)
    with pytest.raises(ValueError):
        _propose(sandbox, skill_name=evil)


def test_propose_text_too_long(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    with pytest.raises(ValueError, match="terlalu panjang"):
        _propose(sandbox, old_text="x" * 25_000)
    with pytest.raises(ValueError, match="terlalu panjang"):
        _propose(sandbox, new_text="y" * 25_000)


def test_propose_unknown_skill(sandbox):
    with pytest.raises(ValueError, match="tidak ditemukan"):
        _propose(sandbox)


def test_storage_hashed_name_and_mode(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    _propose(sandbox)
    expected = (
        sandbox["prop_dir"]
        / f"{hashlib.sha256(IDENTITY.encode()).hexdigest()[:32]}.json"
    )
    assert expected.is_file()
    assert os.stat(expected).st_mode & 0o777 == 0o600
    assert os.stat(sandbox["prop_dir"]).st_mode & 0o777 == 0o700
    raw = json.loads(expected.read_text(encoding="utf-8"))
    assert len(raw) == 1 and raw[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------


def test_get_and_list(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    p1 = _propose(sandbox)
    p2 = _propose(sandbox, title="kedua", new_text=NEW + " v2")
    assert sp.get_proposal(p1["id"], IDENTITY)["title"] == "Perbaiki baris basi"
    assert sp.get_proposal("p-tidak-ada", IDENTITY) is None
    assert [p["id"] for p in sp.list_proposals(IDENTITY)] == [p1["id"], p2["id"]]
    assert len(sp.list_proposals(IDENTITY, status="pending")) == 2
    assert sp.list_proposals(IDENTITY, status="applied") == []
    with pytest.raises(ValueError, match="status tidak dikenal"):
        sp.list_proposals(IDENTITY, status="ngawur")
    # Hasil adalah salinan: mutasi tidak bocor ke simpanan.
    got = sp.get_proposal(p1["id"], IDENTITY)
    got["status"] = "applied"
    assert sp.get_proposal(p1["id"], IDENTITY)["status"] == "pending"


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_apply_pending_patches_file(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    applied = sp.apply_proposal(proposal["id"], IDENTITY)
    assert applied["status"] == "applied"
    assert applied["applied_at"]
    content = entry.read_text(encoding="utf-8")
    assert NEW in content and OLD not in content
    assert content == SKILL_BODY.replace(OLD, NEW)


def test_apply_twice_rejected(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    sp.apply_proposal(proposal["id"], IDENTITY)
    with pytest.raises(ValueError, match="hanya proposal pending"):
        sp.apply_proposal(proposal["id"], IDENTITY)


def test_apply_stale_file_rejected(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    # Seseorang mengubah file manual setelah proposal dibuat.
    manual = SKILL_BODY.replace(OLD, "Baris yang diubah manual oleh orang lain.")
    entry.write_text(manual, encoding="utf-8")
    with pytest.raises(ValueError, match="berubah sejak proposal dibuat"):
        sp.apply_proposal(proposal["id"], IDENTITY)
    # Konten tetap versi manual; status tetap pending.
    assert entry.read_text(encoding="utf-8") == manual
    assert sp.get_proposal(proposal["id"], IDENTITY)["status"] == "pending"


def test_propose_records_content_sha256(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    expected = hashlib.sha256(entry.read_bytes()).hexdigest()
    assert proposal["content_sha256"] == expected
    # Hash juga tersimpan persisten, bukan hanya di hasil kembalian.
    stored = sp.get_proposal(proposal["id"], IDENTITY)
    assert stored["content_sha256"] == expected


def test_apply_rejects_change_elsewhere_in_file(sandbox):
    """TOCTOU: perubahan di bagian lain file (old_text tetap cocok 1x)
    tetap DITOLAK berkat verifikasi SHA-256 — hitungan string saja tidak
    akan menangkap ini."""
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    # Ubah baris LAIN; old_text masih cocok tepat 1 kali.
    tampered = SKILL_BODY.replace(
        "Bagian lain tidak tersentuh.", "Baris disisipkan penyerang."
    )
    assert tampered.count(OLD) == 1
    entry.write_text(tampered, encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 isi file tidak lagi cocok"):
        sp.apply_proposal(proposal["id"], IDENTITY)
    # Konten tetap versi tampered; status tetap pending.
    assert entry.read_text(encoding="utf-8") == tampered
    assert sp.get_proposal(proposal["id"], IDENTITY)["status"] == "pending"


def test_apply_hash_mismatch_message_is_clear(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    entry.write_text(SKILL_BODY + "\nTambahan diam-diam.\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        sp.apply_proposal(proposal["id"], IDENTITY)
    msg = str(excinfo.value)
    assert "berubah sejak proposal dibuat" in msg
    assert "DITOLAK" in msg


def test_hash_covers_raw_bytes_not_normalized_text(sandbox):
    """MINOR-2: byte invalid tidak boleh dinormalisasi sebelum di-hash.

    propose dan verify harus menghitung SHA-256 dari byte mentah yang sama;
    kalau hash dihitung dari teks hasil errors="replace", mutasi byte invalid
    menjadi tak terlihat oleh hash.
    """
    raw = SKILL_BODY.encode("utf-8") + b"\xff\xfe invalid \x80 bytes\n"
    entry = _make_skill(sandbox, "demo", "placeholder")
    entry.write_bytes(raw)
    proposal = _propose(sandbox)
    assert proposal["content_sha256"] == hashlib.sha256(raw).hexdigest()
    # Apply lolos: verify menghitung hash dari byte mentah yang sama.
    applied = sp.apply_proposal(proposal["id"], IDENTITY)
    assert applied["status"] == "applied"
    assert NEW in entry.read_text(encoding="utf-8", errors="replace")


def test_apply_rejects_change_between_verify_and_write(sandbox, monkeypatch):
    """MINOR-1: perubahan file di antara verify dan tulis DITOLAK (fail-closed).

    Mensimulasikan balapan TOCTOU residual: file diubah tepat setelah
    pembacaan terverifikasi, sebelum patch ditulis. Patch harus ditolak dan
    status tetap pending — hasil patch tidak pernah dihitung dari pembacaan
    kedua, dan konten penyerang tidak ikut tertulis bersama patch.
    """
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    original = sp._read_verified

    def _tampering_read(target, prop):
        raw, content = original(target, prop)
        # Penyerang menyisipkan baris jahat setelah verifikasi hash;
        # old_text tetap cocok 1x sehingga kode lama (re-read) akan lolos.
        target.write_bytes(raw + b"\nBaris jahat disisipkan penyerang.\n")
        return raw, content

    monkeypatch.setattr(sp, "_read_verified", _tampering_read)
    with pytest.raises(ValueError, match="berubah antara verifikasi dan penulisan"):
        sp.apply_proposal(proposal["id"], IDENTITY)
    # Patch DITOLAK: new_text tidak masuk, status tetap pending.
    after = entry.read_bytes()
    assert NEW.encode("utf-8") not in after
    assert b"Baris jahat disisipkan penyerang." in after
    assert sp.get_proposal(proposal["id"], IDENTITY)["status"] == "pending"


@pytest.mark.parametrize(
    "corrupt",
    [
        123,  # bukan string
        "ab" * 10,  # terlalu pendek
        "zz" * 32,  # bukan heksadesimal
        "ABCDEF" * 10 + "abcd",  # 64 char tapi huruf besar
        "🔑" * 64,  # 64 char tapi non-ASCII
    ],
)
def test_apply_corrupt_stored_hash_raises_proposal_error(sandbox, corrupt):
    """MINOR-3: content_sha256 korup di store -> ProposalError yang jelas,
    bukan TypeError tak tertangani dari hmac.compare_digest."""
    _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    items = sp._load(IDENTITY)
    entry = sp._require(items, proposal["id"])
    entry["content_sha256"] = corrupt
    sp._save(IDENTITY, items)
    with pytest.raises(sp.ProposalError, match="hash integritas proposal rusak"):
        sp.apply_proposal(proposal["id"], IDENTITY)
    assert sp.get_proposal(proposal["id"], IDENTITY)["status"] == "pending"


def test_apply_legacy_proposal_without_hash(sandbox):
    """Kompatibilitas mundur: proposal lama tanpa content_sha256 jatuh ke
    pemeriksaan hitungan old_text."""
    _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    items = sp._load(IDENTITY)
    entry = sp._require(items, proposal["id"])
    del entry["content_sha256"]
    sp._save(IDENTITY, items)
    # File tidak berubah -> apply tetap lolos via jalur legacy.
    applied = sp.apply_proposal(proposal["id"], IDENTITY)
    assert applied["status"] == "applied"


def test_apply_file_deleted_rejected(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    entry.unlink()
    with pytest.raises(ValueError, match="tidak ditemukan"):
        sp.apply_proposal(proposal["id"], IDENTITY)


def test_apply_public_skill_copy_on_write(sandbox):
    public_entry = _make_skill(sandbox, "demo", SKILL_BODY, scope="public")
    proposal = _propose(sandbox)
    applied = sp.apply_proposal(proposal["id"], IDENTITY)
    assert applied["status"] == "applied"
    # Public tidak tersentuh; patch mendarat di salinan private.
    assert public_entry.read_text(encoding="utf-8") == SKILL_BODY
    private_entry = sandbox["private"] / "demo" / "SKILL.md"
    assert private_entry.is_file()
    assert NEW in private_entry.read_text(encoding="utf-8")


def test_apply_rejected_proposal_refused(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    rejected = sp.reject_proposal(proposal["id"], IDENTITY, "tidak perlu")
    assert rejected["status"] == "rejected"
    assert rejected["rejected_reason"] == "tidak perlu"
    with pytest.raises(ValueError, match="hanya proposal pending"):
        sp.apply_proposal(proposal["id"], IDENTITY)


def test_reject_requires_reason(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    with pytest.raises(ValueError, match="alasan penolakan"):
        sp.reject_proposal(proposal["id"], IDENTITY, "  ")


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def test_rollback_restores_exact_content(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    sp.apply_proposal(proposal["id"], IDENTITY)
    rolled = sp.rollback_proposal(proposal["id"], IDENTITY)
    assert rolled["status"] == "rolled_back"
    assert rolled["rolled_back_at"]
    assert entry.read_text(encoding="utf-8") == SKILL_BODY


def test_rollback_pending_rejected(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    with pytest.raises(ValueError, match="hanya proposal applied"):
        sp.rollback_proposal(proposal["id"], IDENTITY)


def test_rollback_rejected_proposal_refused(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    sp.reject_proposal(proposal["id"], IDENTITY, "batal")
    with pytest.raises(ValueError, match="hanya proposal applied"):
        sp.rollback_proposal(proposal["id"], IDENTITY)


def test_rollback_after_manual_change_refused(sandbox):
    entry = _make_skill(sandbox, "demo", SKILL_BODY)
    proposal = _propose(sandbox)
    sp.apply_proposal(proposal["id"], IDENTITY)
    manual = entry.read_text(encoding="utf-8").replace(NEW, "diubah manual lagi")
    entry.write_text(manual, encoding="utf-8")
    with pytest.raises(ValueError, match="berubah setelah apply"):
        sp.rollback_proposal(proposal["id"], IDENTITY)
    # Status TIDAK berubah; konten manual tetap (tidak ada rollback diam-diam).
    assert sp.get_proposal(proposal["id"], IDENTITY)["status"] == "applied"
    assert entry.read_text(encoding="utf-8") == manual


# ---------------------------------------------------------------------------
# id tak dikenal
# ---------------------------------------------------------------------------


def test_apply_flat_private_skill_promotes_and_rolls_back(sandbox):
    """Skill private berbentuk satu file .md: apply mempromosikan ke folder,
    rollback mengembalikan konten persis."""
    flat = sandbox["private"] / "demo.md"
    flat.write_text(SKILL_BODY, encoding="utf-8")
    proposal = _propose(sandbox)
    sp.apply_proposal(proposal["id"], IDENTITY)
    # Flat dipromosikan jadi folder oleh manage_skill; isi terpatch.
    assert not flat.exists()
    folder_entry = sandbox["private"] / "demo" / "SKILL.md"
    assert folder_entry.is_file()
    assert NEW in folder_entry.read_text(encoding="utf-8")
    rolled = sp.rollback_proposal(proposal["id"], IDENTITY)
    assert rolled["status"] == "rolled_back"
    assert OLD in folder_entry.read_text(encoding="utf-8")
    assert NEW not in folder_entry.read_text(encoding="utf-8")


def test_unknown_id_errors_are_clear(sandbox):
    _make_skill(sandbox, "demo", SKILL_BODY)
    with pytest.raises(ValueError, match="tidak dikenal"):
        sp.apply_proposal("p-tidak-ada", IDENTITY)
    with pytest.raises(ValueError, match="tidak dikenal"):
        sp.reject_proposal("p-tidak-ada", IDENTITY, "x")
    with pytest.raises(ValueError, match="tidak dikenal"):
        sp.rollback_proposal("p-tidak-ada", IDENTITY)
