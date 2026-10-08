"""Proposal perbaikan konten skill — jantung batas keamanan self-improving skills.

Modul ini mencatat *niat* memperbaiki sebuah skill sebagai proposal yang bisa
diaudit, disetujui operator, diterapkan, ditolak, atau di-rollback. Modul ini
bukan jalur pintas: ia adalah gerbang yang memaksa setiap perubahan konten
skill melewati verifikasi ulang dan (di layer tool) persetujuan operator.

BATAS KEAMANAN (ditegakkan di kode, bukan sekadar dokumentasi):

1. ``propose_fix()`` TIDAK PERNAH menulis file skill. Ia hanya MEMBACA file
   target untuk memverifikasi ``old_text``. Satu-satunya fungsi yang memanggil
   ``skills.manage_skill()`` di modul ini adalah ``rollback_proposal()``
   (aksi ``"patch"``); ``apply_proposal()`` menerapkan patch sendiri dari
   SATU pembacaan terverifikasi (tanpa re-read — menutup jendela TOCTOU
   antara verify dan patch), dengan checkpoint via
   ``skills._checkpoint_skill_file``.
2. ``apply_proposal()`` membaca file skill TEPAT SEKALI (``read_bytes``) dan
   memverifikasi SHA-256 BYTE MENTAH seluruh isinya cocok dengan hash yang
   disimpan saat propose. Hasil patch DIHITUNG DARI byte terverifikasi itu
   lalu langsung ditulis — tidak ada pembacaan kedua yang menjadi dasar
   patch, sehingga tidak ada jendela TOCTOU antara verifikasi dan penulisan.
   Pemeriksaan konsistensi sebelum tulis menolak bila file berubah di tengah
   jalan (fail-closed). Bila file berubah sejak proposal (stale patch), patch
   DITOLAK dengan pesan jelas — tidak ada patch buta. Hitungan ``old_text``
   saja tidak cukup karena perubahan di bagian lain file tidak mengubah
   hitungan itu.
3. Tidak ada jalur lain yang mengubah konten skill. ``rollback_proposal()``
   membalik ``new_text`` -> ``old_text`` lewat ``manage_skill("patch")`` yang
   sama (dengan checkpoint otomatis dari ``skills.py``), lalu MEMVERIFIKASI
   konten file benar-benar kembali sebelum menandai status.

Catatan persetujuan: fungsi-fungsi di modul ini TIDAK meminta persetujuan
sendiri. Persetujuan operator terjadi di layer tool (risk INSTALL) SEBELUM
``apply_proposal()`` dipanggil — lihat rancangan ``apply_skill_proposal``.

Penyimpanan: satu file JSON per identity di
``~/.zeline/skill-proposals/<sha256(identity)[:32]>.json``, mode 0600,
tulis atomik (direktori 0700, file temp per-pid + replace), mengikuti pola
``zeline/tasks.py``.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from zeline import config as _config
from zeline import skills as _skills

#: Nama direktori penyimpanan di bawah DATA_DIR.
PROPOSALS_DIR_NAME = "skill-proposals"
#: Batas panjang tiap blok teks proposal (karakter).
MAX_TEXT_CHARS = 20_000

#: Status proposal.
STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_REJECTED = "rejected"
STATUS_ROLLED_BACK = "rolled_back"
_VALID_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_APPLIED, STATUS_REJECTED, STATUS_ROLLED_BACK}
)


class ProposalError(ValueError):
    """Proposal tidak valid atau tidak bisa dijalankan; alasannya informatif."""


# ---------------------------------------------------------------------------
# Penyimpanan
# ---------------------------------------------------------------------------


def _proposal_dir() -> Path:
    return _config.DATA_DIR / PROPOSALS_DIR_NAME


def _key(identity: str) -> str:
    return hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]


def _path(identity: str) -> Path:
    return _proposal_dir() / f"{_key(identity)}.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(identity: str) -> list[dict]:
    """Muat semua proposal milik identity; gagal-tertutup bila file rusak."""
    path = _path(identity)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProposalError(f"file proposal rusak dan tidak bisa dibaca: {exc}")
    if not isinstance(raw, list):
        raise ProposalError("file proposal rusak: isi bukan daftar proposal.")
    items = [entry for entry in raw if isinstance(entry, dict)]
    if len(items) != len(raw):
        raise ProposalError("file proposal rusak: ada entri yang bukan objek.")
    return items


def _save(identity: str, items: list[dict]) -> None:
    """Tulis atomik: direktori 0700, file temp per-pid + replace, hasil 0600."""
    directory = _proposal_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    target = _path(identity)
    # Nama temp unik per-pid: dua penulis bersamaan tidak saling menimpa.
    temporary = target.with_name(f"{target.stem}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ProposalError(f"gagal menyimpan proposal: {exc}")


def _find(items: list[dict], proposal_id: str) -> dict | None:
    for entry in items:
        if entry.get("id") == proposal_id:
            return entry
    return None


def _require(items: list[dict], proposal_id: str) -> dict:
    entry = _find(items, proposal_id)
    if entry is None:
        raise ProposalError(
            f"proposal '{proposal_id}' tidak dikenal. "
            "Gunakan list_proposals() untuk melihat id yang tersedia."
        )
    return entry


# ---------------------------------------------------------------------------
# Resolusi file skill (hanya-baca; dipakai identik oleh propose/apply/rollback)
# ---------------------------------------------------------------------------


def _validate_skill_name(skill_name: str) -> str:
    """Nama harus lolos ``skills._safe_name`` DAN sudah dalam bentuk normal.

    Syarat kedua menutup celah nama yang "lolos" karena dinormalisasi diam-diam
    (mis. ``"../jahat"`` menjadi ``"jahat"`` setelah ``/`` dan ``.`` dibuang):
    input harus persis sama dengan hasil normalisasinya.
    """
    raw = (skill_name or "").strip()
    if not raw:
        raise ProposalError("skill_name tidak boleh kosong.")
    try:
        normalized = _skills._safe_name(raw)
    except ValueError as exc:
        raise ProposalError(f"nama skill tidak valid: {exc}")
    if normalized != raw:
        raise ProposalError(
            f"nama skill tidak valid: gunakan bentuk ternormalisasi "
            f"{normalized!r}, bukan {raw!r}."
        )
    return normalized


def _target_file(skill_name: str, file_path: str) -> Path:
    """Resolusi file skill efektif — HANYA MEMBACA, tidak pernah menulis.

    Memakai ``_locate_unit`` yang sama dengan ``manage_skill``: unit private
    (folder) diutamakan, lalu public. Untuk folder, ``file_path`` divalidasi
    dengan ``_safe_skill_relative`` (bukti containment, bukan sekadar
    allowlist karakter). Untuk unit flat ``.md``, hanya ``SKILL.md`` bermakna.
    """
    located = _skills._locate_unit(skill_name)
    if located is None:
        raise ProposalError(f"skill '{skill_name}' tidak ditemukan.")
    _scope, unit = located
    cleaned = (file_path or _skills.SKILL_ENTRY).strip()
    if unit.is_dir():
        try:
            target = _skills._safe_skill_relative(unit, cleaned)
        except ValueError as exc:
            raise ProposalError(f"file_path tidak valid: {exc}")
    else:
        if cleaned != _skills.SKILL_ENTRY:
            raise ProposalError(
                f"skill '{skill_name}' berbentuk satu file; "
                f"file_path harus '{_skills.SKILL_ENTRY}'."
            )
        target = unit
    if not target.is_file():
        raise ProposalError(
            f"file '{cleaned}' tidak ada di skill '{skill_name}'."
        )
    return target


def _validate_texts(old_text: str, new_text: str, reason: str) -> None:
    if not isinstance(old_text, str) or not old_text:
        raise ProposalError("old_text tidak boleh kosong.")
    if not isinstance(new_text, str) or not new_text.strip():
        raise ProposalError("new_text tidak boleh kosong.")
    if new_text == old_text:
        raise ProposalError("new_text harus berbeda dari old_text.")
    if not isinstance(reason, str) or not reason.strip():
        raise ProposalError("reason tidak boleh kosong: jelaskan kenapa perbaikan ini perlu.")
    if len(old_text) > MAX_TEXT_CHARS:
        raise ProposalError(
            f"old_text terlalu panjang ({len(old_text):,} > {MAX_TEXT_CHARS:,} karakter)."
        )
    if len(new_text) > MAX_TEXT_CHARS:
        raise ProposalError(
            f"new_text terlalu panjang ({len(new_text):,} > {MAX_TEXT_CHARS:,} karakter)."
        )


# ---------------------------------------------------------------------------
# API publik
# ---------------------------------------------------------------------------


def propose_fix(
    skill_name: str,
    identity: str,
    old_text: str,
    new_text: str,
    reason: str,
    file_path: str = "SKILL.md",
    title: str = "",
) -> dict:
    """Catat proposal perbaikan; TIDAK MENYENTUH file skill.

    Validasi keras (gagal -> ``ProposalError``/``ValueError``, bukan proposal
    ngawur): nama lolos ``skills._safe_name`` dalam bentuk ternormalisasi,
    file target ada, ``old_text`` cocok persis tepat 1 kali di isi file saat
    ini, ``new_text`` tidak kosong dan berbeda, ``reason`` tidak kosong, dan
    tiap blok teks <= 20.000 karakter.
    """
    name = _validate_skill_name(skill_name)
    _validate_texts(old_text, new_text, reason)
    cleaned_path = (file_path or _skills.SKILL_ENTRY).strip()
    target = _target_file(name, cleaned_path)
    raw = target.read_bytes()
    content = raw.decode("utf-8", errors="replace")
    count = content.count(old_text)
    if count != 1:
        raise ProposalError(
            f"old_text harus cocok PERSIS 1 kali di '{cleaned_path}' skill "
            f"'{name}' (ditemukan {count} kali). Perbaiki old_text agar unik."
        )
    now = _utcnow()
    proposal = {
        "id": "p-" + uuid.uuid4().hex[:12],
        "skill_name": name,
        "file_path": cleaned_path,
        "title": (title or "").strip(),
        "old_text": old_text,
        "new_text": new_text,
        "reason": reason.strip(),
        # SHA-256 BYTE MENTAH isi file saat propose — bukti integritas
        # anti-TOCTOU yang diverifikasi _read_verified() sebelum apply.
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "status": STATUS_PENDING,
        "created_at": now,
        "applied_at": "",
    }
    items = _load(identity)
    if _find(items, proposal["id"]) is not None:  # praktis mustahil, tapi murah
        raise ProposalError("tabrakan id proposal; coba lagi.")
    items.append(proposal)
    _save(identity, items)
    return dict(proposal)


def get_proposal(proposal_id: str, identity: str) -> dict | None:
    """Ambil satu proposal (salinan; mengubah hasil tidak mengubah simpanan)."""
    entry = _find(_load(identity), proposal_id)
    return dict(entry) if entry is not None else None


def list_proposals(identity: str, status: str | None = None) -> list[dict]:
    """Daftar proposal milik identity, terurut waktu pembuatan."""
    if status is not None and status not in _VALID_STATUSES:
        raise ProposalError(
            f"status tidak dikenal: {status!r} "
            f"(pilih: {', '.join(sorted(_VALID_STATUSES))})."
        )
    items = _load(identity)
    if status is not None:
        items = [entry for entry in items if entry.get("status") == status]
    ordered = sorted(items, key=lambda entry: entry.get("created_at", ""))
    return [dict(entry) for entry in ordered]


def reject_proposal(proposal_id: str, identity: str, reason: str) -> dict:
    """Tolak proposal pending; alasan penolakan wajib dicatat."""
    items = _load(identity)
    entry = _require(items, proposal_id)
    if entry.get("status") != STATUS_PENDING:
        raise ProposalError(
            f"proposal '{proposal_id}' berstatus {entry.get('status')!r}; "
            "hanya proposal pending yang bisa ditolak."
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ProposalError("alasan penolakan tidak boleh kosong.")
    entry["status"] = STATUS_REJECTED
    entry["rejected_at"] = _utcnow()
    entry["rejected_reason"] = reason.strip()
    _save(identity, items)
    return dict(entry)


def _validated_stored_hash(proposal: dict) -> str | None:
    """Ambil ``content_sha256`` dari proposal yang sudah divalidasi.

    Kembalikan ``None`` bila kolom tidak ada (proposal lama: kompatibilitas
    mundur ke pemeriksaan hitungan ``old_text``). Nilai yang korup — bukan
    string heksadesimal SHA-256 ASCII 64 karakter — melempar ``ProposalError``
    dengan pesan jelas, BUKAN ``TypeError`` tak tertangani dari
    ``hmac.compare_digest`` (nilai dari store tidak boleh dipercaya
    mentah-mentah).
    """
    expected = proposal.get("content_sha256")
    if expected is None:
        return None
    ok = (
        isinstance(expected, str)
        and len(expected) == 64
        and expected.isascii()
        and all(c in "0123456789abcdef" for c in expected)
    )
    if not ok:
        raise ProposalError(
            "hash integritas proposal rusak: 'content_sha256' bukan string "
            "heksadesimal SHA-256 yang valid. Proposal DITOLAK — buat proposal "
            "baru dari kondisi file terkini."
        )
    return expected


def _read_verified(target: Path, proposal: dict) -> tuple[bytes, str]:
    """Baca file skill TEPAT SEKALI; kembalikan ``(raw_bytes, teks)`` terverifikasi.

    SHA-256 dihitung dari BYTE MENTAH file (``read_bytes``), bukan dari teks
    yang sudah dinormalisasi ``errors="replace"`` — byte invalid yang
    dinormalisasi diam-diam akan membuat mutasi tak terlihat oleh hash.

    Inilah penegak batas "tanpa patch buta": SETIAP perubahan file setelah
    propose — bahkan yang tidak menyentuh ``old_text`` — mengubah hash dan
    membuat apply DITOLAK. Hitungan kemunculan ``old_text`` sendiri tidak
    cukup: penyerang bisa menambah teks di bagian lain file tanpa mengubah
    hitungan tersebut (TOCTOU klasik), sehingga verifikasi integritas wajib
    berbasis hash seluruh isi file, bukan sekadar pencocokan string.

    Proposal lama (dibuat sebelum kolom ``content_sha256`` ada) jatuh ke
    pemeriksaan hitungan ``old_text`` sebagai kompatibilitas mundur.
    """
    raw = target.read_bytes()
    expected = _validated_stored_hash(proposal)
    if expected is not None:
        actual = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(actual, expected):
            raise ProposalError(
                "file skill berubah sejak proposal dibuat: SHA-256 isi file "
                "tidak lagi cocok dengan hash saat proposal dibuat. "
                "Patch DITOLAK — buat proposal baru dari kondisi file terkini."
            )
    content = raw.decode("utf-8", errors="replace")
    count = content.count(proposal["old_text"])
    if count != 1:
        raise ProposalError(
            f"file skill berubah sejak proposal dibuat: old_text kini cocok "
            f"{count} kali (harus tepat 1). Patch DITOLAK — buat proposal baru "
            "dari kondisi file terkini."
        )
    return raw, content


def _apply_from_verified_read(entry: dict) -> None:
    """Terapkan patch dari SATU pembacaan terverifikasi — tanpa re-read.

    Alur: resolusi read-only -> SATU ``read_bytes`` + verifikasi hash ->
    adopsi copy-on-write ke private -> tulis hasil patch yang DIHITUNG DARI
    byte terverifikasi -> verifikasi pasca-tulis. Hasil patch tidak pernah
    dihitung dari pembacaan kedua, sehingga tidak ada jendela TOCTOU antara
    verify dan patch: file yang berubah setelah verifikasi terdeteksi oleh
    pemeriksaan konsistensi sebelum tulis, dan patch DITOLAK (fail-closed).

    Untuk unit flat, adopsi membungkus konten dengan frontmatter secara
    deterministik (``skills._frontmatter``); ekspektasi konsistensi
    dibandingkan terhadap hasil bungkusan itu, bukan byte mentah.
    """
    name = entry["skill_name"]
    cleaned = (entry["file_path"] or _skills.SKILL_ENTRY).strip()
    target = _target_file(name, cleaned)  # read-only; raise seperti biasa
    located = _skills._locate_unit(name)
    flat = located is not None and not located[1].is_dir()
    raw, content = _read_verified(target, entry)  # SATU-SATUNYA read dasar patch
    # Adopsi ke private (copy-on-write public -> private, atau promosi flat);
    # untuk skill folder private ini no-op sehingga target tidak berpindah.
    skill_dir, note = _skills._adopt_into_private(name)
    if note == "created":
        # Balapan: skill hilang antara resolusi read-only dan adopsi.
        # Jangan tinggalkan folder kosong (seperti _patch_skill).
        with contextlib.suppress(OSError):
            skill_dir.rmdir()
        raise ProposalError(f"skill '{name}' tidak ditemukan.")
    try:
        adopted = _skills._safe_skill_relative(skill_dir, cleaned)
    except ValueError as exc:
        raise ProposalError(f"file_path tidak valid: {exc}")
    # Konten yang seharusnya ada di file adopted: byte terverifikasi, atau
    # versi frontmatter-nya untuk unit flat (hasil deterministik adopsi).
    # Bandingkan terhadap byte mentah terverifikasi — bukan teks yang
    # di-encode ulang — agar file ber-byte invalid tidak salah ditolak.
    expected_current = (
        _skills._frontmatter(name, content).encode("utf-8") if flat else raw
    )
    try:
        current = adopted.read_bytes()
    except OSError:
        current = None
    if current != expected_current:
        raise ProposalError(
            "file skill berubah antara verifikasi dan penulisan: isi file "
            "tidak lagi sama dengan konten terverifikasi. Patch DITOLAK — "
            "buat proposal baru dari kondisi file terkini."
        )
    # Teks dasar patch: decode dari byte ekspektasi (berasal murni dari data
    # terverifikasi, bukan dari pembacaan baru apa pun).
    base_text = expected_current.decode("utf-8", errors="replace")
    if base_text.count(entry["old_text"]) != 1:
        raise ProposalError(
            "old_text tidak unik setelah adopsi file; Patch DITOLAK — buat "
            "proposal baru dari kondisi file terkini."
        )
    patched = base_text.replace(entry["old_text"], entry["new_text"], 1)
    _skills._checkpoint_skill_file(adopted, "skill-patch")
    adopted.write_text(patched, encoding="utf-8")
    _skills._chmod_private(adopted, 0o600)
    # Verifikasi pasca-tulis: file harus PERSIS sama dengan hasil patch yang
    # dihitung — bukan sekadar mengandung new_text.
    try:
        written = adopted.read_bytes()
    except OSError:
        written = None
    if written != patched.encode("utf-8"):
        raise ProposalError(
            "verifikasi pasca-tulis gagal: isi file tidak sama dengan hasil "
            "patch yang dihitung. Proposal TIDAK ditandai applied; pulihkan "
            "lewat checkpoint 'zeline undo' bila perlu."
        )


def _effective_target(proposal: dict) -> Path:
    """Resolusi ulang file efektif (menangani copy-on-write public->private)."""
    return _target_file(proposal["skill_name"], proposal["file_path"])


def apply_proposal(proposal_id: str, identity: str) -> dict:
    """Terapkan proposal pending dari satu pembacaan file yang terverifikasi.

    Persetujuan operator terjadi di layer tool (risk INSTALL) SEBELUM fungsi
    ini dipanggil — fungsi ini sendiri tidak meminta apa pun, ia hanya
    mengeksekusi dengan aman: tolak bila status bukan pending, baca file
    TEPAT SEKALI dan tolak bila SHA-256 byte mentahnya tidak lagi cocok
    dengan hash saat propose (TOCTOU), hitung hasil patch DARI byte
    terverifikasi itu (tanpa re-read), tulis dengan checkpoint otomatis,
    lalu verifikasi konten file persis sama dengan hasil patch sebelum
    menandai ``applied``.
    """
    items = _load(identity)
    entry = _require(items, proposal_id)
    if entry.get("status") != STATUS_PENDING:
        raise ProposalError(
            f"proposal '{proposal_id}' berstatus {entry.get('status')!r}; "
            "hanya proposal pending yang bisa diterapkan."
        )
    _apply_from_verified_read(entry)
    entry["status"] = STATUS_APPLIED
    entry["applied_at"] = _utcnow()
    _save(identity, items)
    return dict(entry)


def rollback_proposal(proposal_id: str, identity: str) -> dict:
    """Kembalikan proposal applied: patch new_text -> old_text, lalu verifikasi.

    Hanya untuk status ``applied``. Karena proposal menyimpan ``old_text`` /
    ``new_text`` lengkap, rollback selalu mungkin tanpa menebak isi file.
    Setiap kegagalan (file berubah setelah apply, patch error, verifikasi
    gagal) melempar ``ProposalError`` TANPA mengubah status — tidak ada
    rollback yang gagal diam-diam.
    """
    items = _load(identity)
    entry = _require(items, proposal_id)
    if entry.get("status") != STATUS_APPLIED:
        raise ProposalError(
            f"proposal '{proposal_id}' berstatus {entry.get('status')!r}; "
            "hanya proposal applied yang bisa di-rollback."
        )
    target = _effective_target(entry)
    content = target.read_text(encoding="utf-8", errors="replace")
    count = content.count(entry["new_text"])
    if count != 1:
        raise ProposalError(
            f"file skill berubah setelah apply: new_text kini cocok {count} "
            f"kali (harus tepat 1). Rollback DITOLAK — pulihkan manual lewat "
            f"checkpoint 'zeline undo'."
        )
    # Satu-satunya pemanggilan manage_skill di jalur rollback (aksi patch saja).
    result = _skills.manage_skill(
        "patch",
        name=entry["skill_name"],
        old_text=entry["new_text"],
        new_text=entry["old_text"],
        file_path=entry["file_path"],
    )
    if result.startswith("ERROR"):
        raise ProposalError(f"rollback gagal diterapkan: {result}")
    after = _effective_target(entry).read_text(encoding="utf-8", errors="replace")
    if entry["old_text"] not in after:
        raise ProposalError(
            "rollback dilaporkan berhasil tetapi old_text tidak kembali ke file; "
            "status proposal TIDAK diubah."
        )
    entry["status"] = STATUS_ROLLED_BACK
    entry["rolled_back_at"] = _utcnow()
    _save(identity, items)
    return dict(entry)
