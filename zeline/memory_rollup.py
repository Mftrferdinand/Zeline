"""Penggulungan (rollup) fakta-fakta lama menjadi ringkasan berprovenance.

Memory menumpuk fakta dari waktu ke waktu. Fakta lama tetap berharga sebagai
arsip, tetapi satu per satu jarang dibaca lagi — dan setiap fakta ikut
dihitung dalam batas ``MAX_FACTS_PER_IDENTITY`` serta memperpanjang konteks
yang disuntik ke prompt.

Modul ini melipat fakta yang sudah tua atau ber-confidence rendah menjadi SATU
ringkasan ekstratif deterministik per kelompok (``kind`` + ``source``), tanpa
memanggil model apa pun (zero-token, tanpa network). Pola ringkasannya
mengikuti ``zeline/compaction.py``: kalimat terpenting tiap fakta digabung
menjadi daftar bullet yang berbatas panjang.

KEPUTUSAN DESAIN — dipakai pola **"tandai"**; dua alternatif ditolak beralasan:

1. **Dipilih: penanda di file pendamping (sidecar).** Fakta asal TIDAK dihapus
   dan TIDAK dipindah keluar dari file memory — tetap terbaca penuh lewat
   ``records()`` / ``retrieve()`` / ``prompt_block()``. Penanda "sudah
   digulung" (peta id-fakta -> id ringkasan; id fakta = hash teks +
   pembeda source & created_at) beserta metadata
   provenance (``source_fact_ids``, source asal grup, teks ringkasan)
   disimpan di ``<MEMORY_DIR>/rollups/<hash-identity>.json``. Ditulis atomis
   0600 di bawah lock identitas yang sama dengan ``memory.py`` sehingga
   tidak balapan dengan ``add()`` / ``remove()`` / ``consolidate()``.
2. **Ditolak: menandai langsung di record** (mis. kunci ``rolled_up`` pada
   dict fakta). ``memory._coerce_record`` membangun ulang setiap record
   dengan daftar kunci yang tetap — kunci tambahan **dihilangkan diam-diam**
   pada pembacaan berikutnya, dan ``add()``/``remove()``/``consolidate()``
   menulis ulang file dari record yang sudah terkoersi sehingga penandanya
   musnah permanen. Mengubah ``memory.py`` untuk mempertahankan kunci asing
   dilarang oleh mandat tugas ini, jadi opsi ini tidak sound.
3. **Ditolak: mengarsipkan fakta keluar dari file live.** Fakta yang
   dipindah keluar berhenti terbaca oleh ``records()`` / ``retrieve()`` /
   ``prompt_block()`` — mengubah perilaku baca ``memory.py`` bagi semua
   konsumen lain. Mandat menuntut fakta asal "tetap bisa dibaca".

KEAMANAN (aturan anti-pencucian / "washing"):

- Ringkasan yang berasal dari fakta ``*-sync`` (data pihak ketiga yang tidak
  tepercaya: email, kalender, GitHub) WAJIB tetap ber-source untrusted.
  Aturannya satu arah dan konservatif: sebuah grup dinyatakan untrusted bila
  **satu saja** fakta anggotanya ``_is_sync_source`` — ringkasannya memakai
  ``source="rollup-sync"`` (cocok dengan ``_is_sync_source``) sehingga
  ``MemoryStore._render_records`` menaruhnya di blok
  ``<untrusted_external_data>``, tidak pernah di blok fakta tepercaya.
- Ringkasan grup non-sync memakai ``source="rollup"`` dan ``kind="rollup"``:
  ia dirender di blok untrusted berlabel ``[rollup]`` (bucket fallback
  ``_render_records``) — jujur soal authorship (ditulis agen, bukan verbatim
  user) dan tidak pernah naik tingkat kepercayaan. Kontaminasi hanya mengalir
  satu arah: untrusted -> ringkasan untrusted. Tidak ada fakta untrusted
  yang "dicuci" menjadi tepercaya lewat ringkasan.

IDEMPOTENSI: ``rollup()`` dua kali -> laporan kedua melaporkan "tidak ada
yang dikerjakan" (``summaries_created == 0``). Dijamin tiga lapis: (a) fakta
yang sudah digulung dilewati via peta sidecar; (b) record ``kind="rollup"``
tidak pernah menjadi kandidat; (c) id ringkasan deterministik dari hash
fakta asal (fakta yang sama -> id yang sama).

CARA PAKAI (trigger eksplisit oleh operator — modul ini TIDAK mendaftarkan
cron/hook otomatis apa pun, dan tidak menyentuh ``memory.py``):

    from zeline import memory_rollup

    # Simulasi dulu tanpa mengubah apa pun:
    lap = memory_rollup.rollup("telegram:123", dry_run=True)
    print(lap.to_dict())

    # Eksekusi nyata:
    lap = memory_rollup.rollup("telegram:123", max_age_days=90, min_confidence=0.5)

    # Audit & koreksi:
    for r in memory_rollup.list_rollups("telegram:123"):
        print(r["rollup_id"], r["fact_count"], r["group_source"])
    memory_rollup.unroll("telegram:123", "<rollup_id>")  # hapus ringkasan, buka penanda

CARA DISABLE: tidak ada yang perlu dimatikan — perilaku default modul ini
adalah *tidak melakukan apa pun* kecuali ``rollup()`` dipanggil eksplisit.
Tidak ada cron, tidak ada hook, tidak ada thread latar.

RINGKASAN VIA LLM (OPT-IN): lihat ``rollup_llm()``. Default MATI TOTAL —
tidak ada network/model call kecuali operator memanggil dengan
``use_llm=True`` DAN menyerahkan callable ``summarizer`` eksplisit.
Trade-off biaya token didokumentasikan di docstring ``rollup_llm()``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from zeline import memory as _memory

__all__ = [
    "ROLLUP_KIND",
    "ROLLUP_SOURCE",
    "ROLLUP_SYNC_SOURCE",
    "MIN_GROUP_SIZE",
    "MAX_SUMMARY_CHARS",
    "RollupGroupResult",
    "RollupReport",
    "fact_id",
    "rollup",
    "rollup_llm",
    "list_rollups",
    "unroll",
]

#: Kind record untuk ringkasan hasil gulungan. Record ber-kind ini tidak pernah
#: menjadi kandidat rollup ulang (mencegah ringkasan-dari-ringkasan yang
#: mengaburkan provenance).
ROLLUP_KIND = "rollup"

#: Source ringkasan dari grup fakta non-sync. Bukan "user"/"reflection" supaya
#: authorship-nya jujur (ditulis agen saat menggulung) — di ``_render_records``
#: ia jatuh ke bucket fallback dan tampil di blok untrusted berlabel [rollup].
ROLLUP_SOURCE = "rollup"

#: Source ringkasan dari grup yang memuat fakta ``*-sync``. Sengaja berakhiran
#: "-sync" supaya ``memory._is_sync_source("rollup-sync")`` True dan
#: ``_render_records`` menaruhnya di blok ``<untrusted_external_data>``.
#: Ini lapis anti-pencucian: fakta untrusted tidak pernah "dicuci" menjadi
#: tepercaya lewat ringkasan.
ROLLUP_SYNC_SOURCE = "rollup-sync"

#: Ukuran grup minimum agar layak digulung. Satu fakta yang digulung sendirian
#: menghasilkan ringkasan yang isinya ~fakta itu sendiri — noise tanpa nilai.
#: Singleton dibiarkan apa adanya (tidak ditandai) supaya bisa ikut digulung
#: nanti bila grupnya bertambah.
MIN_GROUP_SIZE = 2

#: Batas kalimat per ringkasan dan batas karakter per kalimat / ringkasan —
#: sejajar dengan gaya batas di ``compaction.py`` (MAX_ASKS/MAX_ASK_CHARS/
#: MAX_DIGEST_CHARS) supaya ringkasan tidak menjadi masalah konteks baru.
MAX_SENTENCES_PER_SUMMARY = 8
MAX_SENTENCE_CHARS = 220
MAX_SUMMARY_CHARS = 3000

#: Versi skema file sidecar. Naikkan bila format berubah dan tambahkan migrasi.
_SIDECAR_VERSION = 1

#: Callable peringkas: (kind_grup, source_grup, fakta_terurut) -> badan teks.
#: Dipakai ``rollup()`` (ekstratif deterministik) dan ``rollup_llm()``
#: (disediakan operator). Body lalu dibungkus header/footer provenance yang
#: sama oleh ``_wrap_summary`` apa pun summarizer-nya.
Summarizer = Callable[[str, str, list[dict[str, Any]]], str]

_SENTENCE_RE = re.compile(r"[^.!?…\n]+[.!?…]+")


def fact_id(text: str, source: str = "", created_at: float = 0.0) -> str:
    """Id stabil sebuah fakta: SHA-256 heks dari (source, created_at, teks).

    Record memory tidak punya id bawaan, jadi provenance memakai hash —
    stabil melewati ``_coerce_record`` (ketiga komponen tidak diubah oleh
    koersi) dan cukup untuk menautkan ringkasan ke fakta asalnya saat audit.

    Pembeda ``source`` + ``created_at`` itu WAJIB, bukan opsional: dua fakta
    berteks SAMA dari source berbeda (atau waktu berbeda) adalah fakta yang
    berbeda — hash teks saja membuat keduanya bertabrakan dan ringkasan
    menunjuk ke fakta yang salah. Payload memakai pemisah NUL supaya
    ``("ab", "c")`` tidak bisa sama dengan ``("a", "bc")``.

    Kompatibilitas: pemanggilan ``fact_id(teks)`` tanpa pembeda menghasilkan
    PERSIS hash teks saja seperti sebelumnya — jadi ``summary_hash``
    ringkasan dan sidecar lama tetap cocok tanpa migrasi apa pun.
    """
    try:
        ts = float(created_at or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    src = str(source or "")
    if not src and ts == 0.0:
        # Jalur lama: hash teks saja (kompatibel dengan sidecar lama).
        payload = str(text)
    else:
        payload = f"{src}\x00{ts!r}\x00{str(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_fact_id(record: dict[str, Any]) -> str:
    """``fact_id`` untuk fakta SUMBER: selalu memakai pembeda source + created_at.

    Dipakai untuk ``rolled_fact_ids`` dan ``source_fact_ids`` — DUA fakta
    berteks sama dari source/waktu berbeda tidak boleh berbagi id. Id
    ringkasan (``summary_hash``) sengaja TIDAK memakai ini: ia dicocokkan
    ulang di ``unroll`` dari teks record ringkasan dan harus tetap
    kompatibel dengan sidecar lama.
    """
    return fact_id(
        record.get("text", ""),
        source=str(record.get("source") or ""),
        created_at=record.get("created_at", 0.0),
    )


def _legacy_fact_id(record: dict[str, Any]) -> str:
    """Id fakta gaya sidecar LAMA: SHA-256 teks saja (tanpa pembeda source/created_at).

    Hanya dipakai untuk PENERIMAAN saat pengecekan keanggotaan — penulisan
    baru selalu memakai ``_source_fact_id`` (pembeda wajib; lihat docstring
    ``fact_id``). ``fact_id(teks)`` tanpa pembeda menghasilkan persis hash
    teks saja, jadi ini cocok dengan sidecar lama tanpa migrasi apa pun.
    """
    return fact_id(record.get("text", ""))


def _roll_match_kind(
    rolled_map: dict[str, str], record: dict[str, Any]
) -> str | None:
    """Jenis kecocokan record terhadap ``rolled_map``: ``"exact"`` | ``"legacy"`` | None.

    - ``"exact"``: id gaya baru (teks+source+created_at) cocok — record ini
      PASTI sudah digulung.
    - ``"legacy"``: HANYA hash teks saja yang cocok. Sidecar versi lama
      menyimpan ``rolled_fact_ids`` sebagai hash teks saja, sehingga fakta
      BARU dan BERBEDA (teks sama, source/created_at berbeda) juga cocok di
      sini — kecocokannya TIDAK PASTI. Pemanggil harus memperlakukannya
      fail-closed (lewati run ini, hindari ringkasan duplikat) TAPI
      menghitungnya di counter terpisah (``skipped_legacy_match``), bukan
      ``skipped_already_rolled`` — lihat fase A ``_rollup_impl``.
    - None: tidak cocok dengan keduanya — belum digulung.
    """
    if _source_fact_id(record) in rolled_map:
        return "exact"
    if _legacy_fact_id(record) in rolled_map:
        return "legacy"
    return None


def _is_rolled(rolled_map: dict[str, str], record: dict[str, Any]) -> bool:
    """True bila record sudah digulung: cocok dengan id gaya BARU atau LEGACY.

    Wrapper bool atas ``_roll_match_kind`` — untuk pemakaian yang memang
    hanya butuh jawab ya/tidak. Pemakaian yang butuh membedakan jenis
    kecocokan (counter jujur di fase A ``_rollup_impl``, audit di
    ``unroll``) memakai ``_roll_match_kind`` langsung.
    """
    return _roll_match_kind(rolled_map, record) is not None


def _rollup_id_for(source_fact_ids: list[str]) -> str:
    """Id ringkasan yang deterministik dari fakta asalnya.

    Fakta yang sama -> id yang sama. Ini lapis ketiga idempotensi: bahkan bila
    peta sidecar hilang/rusak sebagian, fakta yang sama tidak akan melahirkan
    dua ringkasan ber-id berbeda dari satu proses yang sama.
    """
    digest = hashlib.sha256("|".join(sorted(source_fact_ids)).encode("utf-8")).hexdigest()
    return "rl_" + digest[:16]


def _sidecar_path(identity: str) -> Path:
    """Path file pendamping rollup; di-resolve saat runtime (bukan import)."""
    return _memory.MEMORY_DIR / "rollups" / f"{_memory._key(identity)}.json"


def _blank_sidecar(identity: str) -> dict[str, Any]:
    return {
        "version": _SIDECAR_VERSION,
        "identity": identity,
        "rollups": [],
        "rolled_fact_ids": {},
    }


def _read_sidecar(identity: str) -> tuple[dict[str, Any] | None, str | None]:
    """Baca sidecar; kembalikan (data, None) atau (None, pesan_error).

    Sidecar korup -> fail-closed: pemanggil membatalkan rollup daripada
    menggulung buta tanpa tahu fakta mana yang sudah digulung (itu akan
    melahirkan ringkasan ganda — duplikasi, bukan data loss, tapi tetap
    melanggar janji idempotensi).
    """
    path = _sidecar_path(identity)
    if not path.exists():
        return _blank_sidecar(identity), None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, (
            f"sidecar rollup korup/tidak terbaca ({path.name}): {exc}; "
            "rollup dibatalkan (fail-closed) — perbaiki/hapus file sidecar "
            "secara manual bila yakin."
        )
    if (
        not isinstance(data, dict)
        or data.get("version") != _SIDECAR_VERSION
        or not isinstance(data.get("rollups"), list)
        or not isinstance(data.get("rolled_fact_ids"), dict)
    ):
        return None, (
            f"sidecar rollup berformat tak dikenal ({path.name}); rollup "
            "dibatalkan (fail-closed)."
        )
    return data, None


def _write_sidecar(identity: str, data: dict[str, Any]) -> None:
    """Tulis sidecar atomis 0600 — pola yang sama seperti ``memory._write``."""
    directory = _memory.MEMORY_DIR / "rollups"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    path = _sidecar_path(identity)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _lead_sentence(text: str) -> str:
    """Kalimat terpenting sebuah fakta = kalimat pertamanya (ekstratif).

    Deterministik, tanpa model: kalimat pertama biasanya memuat klaim utama
    ("User suka kopi. ..."), sisanya elaborasi. Dibatasi
    ``MAX_SENTENCE_CHARS`` ala ``compaction.MAX_ASK_CHARS``.
    """
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return ""
    match = _SENTENCE_RE.search(collapsed)
    sentence = match.group(0).strip() if match else collapsed
    if len(sentence) > MAX_SENTENCE_CHARS:
        sentence = sentence[:MAX_SENTENCE_CHARS].rstrip() + "…"
    return sentence


def _extractive_body(
    kind: str, source: str, facts: list[dict[str, Any]]
) -> str:
    """Badan ringkasan ekstratif: satu bullet per kalimat-terpenting-per-fakta.

    ``facts`` harus sudah terurut stabil oleh pemanggil (tertua dulu) supaya
    keluarannya deterministik. Kalimat duplikat digabung (satu fakta boleh
    mengulang klaim fakta lain tanpa menggandakan bullet).
    """
    del kind, source  # badan tidak tergantung grup; header/footer yang mencatatnya
    sentences: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        sentence = _lead_sentence(fact.get("text", ""))
        if sentence and sentence not in seen:
            seen.add(sentence)
            sentences.append(sentence)
    if not sentences:
        return "- (tidak ada kalimat yang bisa diekstrak)"
    shown = sentences[:MAX_SENTENCES_PER_SUMMARY]
    lines = [f"- {sentence}" for sentence in shown]
    rest = len(sentences) - len(shown)
    if rest > 0:
        lines.append(
            f"- (+{rest} poin lain — id fakta asal lengkap tersimpan di source_fact_ids)"
        )
    return "\n".join(lines)


def _wrap_summary(kind: str, source: str, fact_count: int, body: str) -> str:
    """Bungkus badan ringkasan dengan header/footer provenance yang seragam.

    Header/footer ini ditulis modul (bukan model), jadi framing provenance-nya
    identik baik untuk jalur ekstratif maupun jalur LLM opt-in.
    """
    header = f"[rollup] Ringkasan {fact_count} fakta · kind={kind} · source={source}"
    footer = "Fakta asal tetap tersimpan utuh dan dapat diaudit (list_rollups / unroll)."
    text = f"{header}\n{body.strip()}\n{footer}"
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS] + "\n… [ringkasan dipotong]"
    return text


@dataclass
class RollupGroupResult:
    """Hasil untuk satu grup yang digulung."""

    rollup_id: str
    kind: str  #: kind grup asal (mis. "fact")
    source: str  #: source grup asal (mis. "gmail-sync")
    summary_source: str  #: source record ringkasan ("rollup" / "rollup-sync")
    untrusted: bool  #: True bila grup memuat fakta *-sync
    fact_count: int
    source_fact_ids: list[str]  #: id fakta asal (fact_id + pembeda source & created_at)
    text: str  #: teks ringkasan penuh (yang disimpan sebagai record)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollup_id": self.rollup_id,
            "kind": self.kind,
            "source": self.source,
            "summary_source": self.summary_source,
            "untrusted": self.untrusted,
            "fact_count": self.fact_count,
            "source_fact_ids": list(self.source_fact_ids),
            "text": self.text,
        }


@dataclass
class RollupReport:
    """Laporan satu pemanggilan ``rollup()`` / ``rollup_llm()``."""

    identity: str
    dry_run: bool
    groups: list[RollupGroupResult] = field(default_factory=list)
    facts_rolled: int = 0
    summaries_created: int = 0
    skipped_expired: int = 0
    skipped_already_rolled: int = 0
    # Kecocokan legacy SAJA (hash teks-saja; teks sama tapi source/created_at
    # berbeda): dilewati fail-closed di run ini, dihitung terpisah supaya
    # counter skipped_already_rolled tetap jujur. legacy_matched_facts
    # menyimpan id gaya BARU fakta yang cocok legacy — operator bisa melihat
    # & bertindak manual (unroll entri lama / forget fakta / biarkan).
    skipped_legacy_match: int = 0
    legacy_matched_facts: list[str] = field(default_factory=list)
    skipped_singleton: int = 0
    skipped_full: int = 0
    skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def nothing_to_do(self) -> bool:
        """True bila tidak ada ringkasan yang dibuat pada pemanggilan ini."""
        return self.summaries_created == 0 and self.facts_rolled == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialisasi laporan (dipakai contoh di docstring modul)."""
        return {
            "identity": self.identity,
            "dry_run": self.dry_run,
            "nothing_to_do": self.nothing_to_do,
            "groups": [group.to_dict() for group in self.groups],
            "facts_rolled": self.facts_rolled,
            "summaries_created": self.summaries_created,
            "skipped_expired": self.skipped_expired,
            "skipped_already_rolled": self.skipped_already_rolled,
            "skipped_legacy_match": self.skipped_legacy_match,
            "legacy_matched_facts": list(self.legacy_matched_facts),
            "skipped_singleton": self.skipped_singleton,
            "skipped_full": self.skipped_full,
            "skipped_duplicate": self.skipped_duplicate,
            "errors": list(self.errors),
        }


@dataclass
class _PlannedGroup:
    """Grup kandidat hasil fase seleksi (fase A ``_rollup_impl``).

    ``facts`` adalah SALINAN record (snapshot) — summarizer di fase B (yang
    berjalan TANPA lock dan bisa berupa network call) menerima data yang
    tidak akan berubah di bawah kaki kita.
    """

    kind: str
    source: str
    facts: list[dict[str, Any]]
    fids: list[str]
    rollup_id: str
    summary_source: str
    untrusted: bool


def _rollup_impl(
    identity: str,
    *,
    summarize_body: Summarizer,
    max_age_days: float,
    min_confidence: float,
    dry_run: bool,
) -> RollupReport:
    """Mesin rollup bersama untuk jalur ekstratif dan jalur LLM opt-in.

    Berjalan dalam TIGA fase supaya network call tidak pernah menahan lock
    identitas:

    - **Fase A (di bawah lock identitas):** baca record + sidecar, pilih
      kandidat, kelompokkan per (kind, source), dan snapshot fakta tiap
      grup. Lock yang dipakai SAMA dengan ``MemoryStore``
      (``memory._lock_for``) — jadi seleksi tidak balapan dengan
      ``add()``/``remove()``/``consolidate()``.
    - **Fase B (TANPA lock):** panggil ``summarize_body`` per grup. Di jalur
      ``rollup_llm`` ini BISA berupa network call ke model — lock identitas
      TIDAK dipegang supaya operasi identity lain tidak antre selama
      network hang. Kegagalan summarizer fail-closed per grup (grup itu
      dilewati, grup lain lanjut, fakta tidak ditandai).
    - **Fase C (di bawah lock; dilewati total saat dry_run):** baca ulang
      record + sidecar, validasi ulang tiap grup (faktanya masih hidup?
      belum digulung thread lain selagi fase B berjalan?), lalu commit yang
      lolos: tulis record ringkasan + tandai fakta di sidecar.

    Tidak ada panggilan ``MemoryStore`` di dalam lock (lock-nya bukan
    reentrant) — baca/tulis memakai primitif ``memory._read`` /
    ``memory._write`` secara langsung.
    """
    report = RollupReport(identity=identity, dry_run=dry_run)
    lock = _memory._lock_for(_memory._key(identity))
    now = time.time()

    def _finalize(
        group: _PlannedGroup, text: str, state: dict[str, Any]
    ) -> RollupGroupResult | None:
        """Satu grup siap-ringkas -> hasil final, atau None bila harus dilewati.

        ``state`` membawa snapshot untuk pengecekan: ``live_fids`` (None
        untuk dry_run — validasi ulang hanya di jalur tulis),
        ``rolled_map``, ``live_count``, ``live_chars``, ``existing_texts``.
        Counter skip/error ditulis langsung ke ``report``.
        """
        live_fids = state["live_fids"]
        if live_fids is not None:
            # Fakta bisa berubah selagi summarizer berjalan di fase B
            # (di-remove / digulung thread lain) — validasi ulang di sini,
            # fail-closed per grup.
            missing = [fid for fid in group.fids if fid not in live_fids]
            already = [
                fid
                for fid, fact in zip(group.fids, group.facts)
                if _is_rolled(state["rolled_map"], fact)
            ]
            # NOTE: identik dengan versi inline sebelumnya —
            # ``fid in rolled_map or _legacy_fact_id(fact) in rolled_map`` —
            # karena ``group.fids`` dibangun di fase A sebagai
            # ``_source_fact_id(record)`` dari record yang sama dengan
            # ``group.facts`` (salinan dict tidak mengubah komponen id).
            # Memakai helper supaya logika keanggotaan tidak divergen
            # diam-diam bila aturan kecocokan berubah.
            if missing or already:
                if not missing and len(already) == len(group.fids):
                    # Seluruh grup digulung thread lain selagi fase B
                    # berjalan — setara skipped_already_rolled di fase A,
                    # bukan error.
                    report.skipped_already_rolled += len(group.facts)
                else:
                    report.errors.append(
                        f"grup kind={group.kind} source={group.source}: fakta "
                        "berubah selama summarizer berjalan (dihapus/digulung "
                        "thread lain); grup dilewati, tidak ada yang ditandai."
                    )
                return None
        if text in state["existing_texts"]:
            report.skipped_duplicate += len(group.facts)
            return None
        # Batas kapasitas yang sama seperti add(): jangan memaksa ringkasan
        # masuk ke memory yang sudah penuh — grup dilewati (tidak ditandai)
        # supaya bisa dicoba lagi setelah ada ruang.
        if (
            state["live_count"] + 1 > _memory.MAX_FACTS_PER_IDENTITY
            or state["live_chars"] + len(text) > _memory.MAX_CHARACTERS_PER_IDENTITY
        ):
            report.skipped_full += len(group.facts)
            return None
        result = RollupGroupResult(
            rollup_id=group.rollup_id,
            kind=group.kind,
            source=group.source,
            summary_source=group.summary_source,
            untrusted=group.untrusted,
            fact_count=len(group.facts),
            source_fact_ids=list(group.fids),
            text=text,
        )
        # Cadangan kapasitas untuk grup berikutnya dalam run yang sama.
        state["live_count"] += 1
        state["live_chars"] += len(text)
        state["existing_texts"].add(text)
        return result

    # --- Fase A (di bawah lock): pilih kandidat, kelompokkan, snapshot.
    planned: list[_PlannedGroup] = []
    preview: dict[str, Any] = {}
    with lock:
        path = _memory._path(identity)
        records = _memory._read(path)
        live = _memory._live(records, now)
        report.skipped_expired = len(records) - len(live)

        sidecar, sidecar_error = _read_sidecar(identity)
        if sidecar_error is not None:
            # Fail-closed: sidecar korup -> jangan menggulung buta.
            report.errors.append(sidecar_error)
            return report
        assert sidecar is not None
        rolled_map: dict[str, str] = sidecar.get("rolled_fact_ids", {})

        # Pilih kandidat: tua ATAU ber-confidence rendah; lewati yang sudah
        # digulung, yang kedaluwarsa, dan record ringkasan itu sendiri.
        candidates: list[tuple[dict[str, Any], str]] = []
        for record in live:
            if record.get("kind") == ROLLUP_KIND:
                continue  # ringkasan tidak pernah digulung ulang
            if record.get("source") in (ROLLUP_SOURCE, ROLLUP_SYNC_SOURCE):
                continue  # sabuk + suspender: bukan fakta sumber
            fid = _source_fact_id(record)
            match_kind = _roll_match_kind(rolled_map, record)
            if match_kind == "legacy":
                # Fail-closed tapi jujur: hanya cocok via hash teks-saja —
                # bisa jadi fakta BARU yang berbeda (teks sama,
                # source/created_at berbeda) yang tidak pernah digulung.
                # Lewati run ini (hindari ringkasan duplikat) TAPI catat di
                # counter terpisah, bukan sebagai skipped_already_rolled;
                # id gaya barunya dicatat supaya operator bisa menindak
                # manual (unroll entri lama / forget fakta / biarkan).
                report.skipped_legacy_match += 1
                report.legacy_matched_facts.append(fid)
                continue
            if match_kind == "exact":
                report.skipped_already_rolled += 1
                continue
            try:
                created_at = float(record.get("created_at", now))
            except (TypeError, ValueError):
                created_at = now
            try:
                confidence = float(record.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            age_days = (now - created_at) / 86400.0
            if age_days > max_age_days or confidence < min_confidence:
                candidates.append((record, fid))

        # Kelompokkan per (kind, source); urutan deterministik.
        grouped: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {}
        for record, fid in candidates:
            key = (str(record.get("kind") or "fact"), str(record.get("source") or "user"))
            grouped.setdefault(key, []).append((record, fid))

        for kind, source in sorted(grouped):
            members = sorted(
                grouped[(kind, source)],
                key=lambda member: (
                    float(member[0].get("created_at", 0.0)),
                    member[0]["text"],
                ),
            )
            if len(members) < MIN_GROUP_SIZE:
                report.skipped_singleton += len(members)
                continue
            # Snapshot salinan record: summarizer di fase B menerima data
            # yang tidak akan berubah di bawah kaki kita.
            facts = [dict(record) for record, _fid in members]
            fids = [fid for _record, fid in members]
            # Aturan kontaminasi satu arah: satu saja fakta *-sync di grup ->
            # seluruh ringkasan untrusted. Jangan pernah "mencuci".
            untrusted = any(
                _memory._is_sync_source(fact.get("source")) for fact in facts
            )
            summary_source = ROLLUP_SYNC_SOURCE if untrusted else ROLLUP_SOURCE
            planned.append(
                _PlannedGroup(
                    kind=kind,
                    source=source,
                    facts=facts,
                    fids=fids,
                    rollup_id=_rollup_id_for(fids),
                    summary_source=summary_source,
                    untrusted=untrusted,
                )
            )
        # Snapshot untuk pratinjau dry_run (tidak ada tulis apa pun).
        preview = {
            "live_fids": None,
            "rolled_map": {},
            "live_count": len(live),
            "live_chars": sum(len(record["text"]) for record in live),
            "existing_texts": {record["text"] for record in records},
        }

    # --- Fase B (TANPA lock): panggil summarizer per grup.
    # Di jalur rollup_llm ini bisa berupa network call — lock identitas
    # TIDAK dipegang supaya operasi identity lain tidak antre selama
    # network hang.
    ready: list[tuple[_PlannedGroup, str]] = []
    for group in planned:
        try:
            body = summarize_body(group.kind, group.source, group.facts)
        except Exception as exc:  # noqa: BLE001 — summarizer LLM bisa gagal apa pun
            # Fail-closed per grup: fakta tidak ditandai, grup lain lanjut.
            report.errors.append(
                f"grup kind={group.kind} source={group.source}: summarizer gagal "
                f"({type(exc).__name__}: {exc}); grup dilewati, fakta tidak ditandai."
            )
            continue
        text = _wrap_summary(group.kind, group.source, len(group.facts), str(body or ""))
        ready.append((group, text))

    # --- Fase C: finalisasi; commit hanya untuk jalur tulis (bukan dry_run).
    finalized: list[tuple[RollupGroupResult, list[dict[str, Any]]]] = []
    if dry_run:
        for group, text in ready:
            result = _finalize(group, text, preview)
            if result is not None:
                finalized.append((result, group.facts))
    else:
        with lock:
            # Baca ulang: thread lain bisa menambah/menghapus fakta atau
            # menggulung grup yang sama selagi fase B berjalan.
            path = _memory._path(identity)
            records = _memory._read(path)
            live = _memory._live(records, now)
            sidecar, sidecar_error = _read_sidecar(identity)
            if sidecar_error is not None:
                # Fail-closed: sidecar korup di tengah jalan.
                report.errors.append(sidecar_error)
                return report
            assert sidecar is not None
            rolled_map = sidecar.get("rolled_fact_ids", {})
            state: dict[str, Any] = {
                "live_fids": {_source_fact_id(record) for record in live},
                "rolled_map": rolled_map,
                "live_count": len(live),
                "live_chars": sum(len(record["text"]) for record in live),
                "existing_texts": {record["text"] for record in records},
            }
            for group, text in ready:
                result = _finalize(group, text, state)
                if result is not None:
                    finalized.append((result, group.facts))
            if finalized:
                summary_records = []
                # Confidence ringkasan = confidence terlemah anggotanya:
                # ringkasan hanya sekuat fakta terlemah yang diringkasnya.
                commit_confidences: list[float] = []
                for result, facts in finalized:
                    confidences = []
                    for fact in facts:
                        try:
                            confidences.append(float(fact.get("confidence", 1.0)))
                        except (TypeError, ValueError):
                            confidences.append(1.0)
                    summary_confidence = max(
                        0.0, min(1.0, min(confidences, default=1.0))
                    )
                    commit_confidences.append(summary_confidence)
                    summary_records.append(
                        {
                            "text": result.text,
                            "kind": ROLLUP_KIND,
                            "source": result.summary_source,
                            "confidence": summary_confidence,
                            "created_at": now,
                            "expires_at": None,
                        }
                    )
                # Tulis dari SEMUA record file (bukan cuma yang live): record
                # expired yang tidak tersentuh harus selamat — pola yang sama
                # seperti MemoryStore.restore().
                _memory._write(path, records + summary_records)
                for (result, _facts), summary_confidence in zip(
                    finalized, commit_confidences
                ):
                    sidecar["rollups"].append(
                        {
                            "rollup_id": result.rollup_id,
                            "created_at": now,
                            "group_kind": result.kind,
                            "group_source": result.source,
                            "summary_source": result.summary_source,
                            "untrusted": result.untrusted,
                            "fact_count": result.fact_count,
                            "source_fact_ids": list(result.source_fact_ids),
                            "summary_hash": fact_id(result.text),
                            "confidence": summary_confidence,
                            "text": result.text,
                        }
                    )
                    for fid in result.source_fact_ids:
                        rolled_map[fid] = result.rollup_id
                _write_sidecar(identity, sidecar)

    report.groups = [result for result, _facts in finalized]
    report.facts_rolled = sum(group.fact_count for group in report.groups)
    report.summaries_created = len(report.groups)
    return report


def rollup(
    identity: str,
    *,
    max_age_days: float = 90,
    min_confidence: float = 0.5,
    dry_run: bool = False,
) -> RollupReport:
    """Gulung fakta lama/ber-confidence rendah menjadi ringkasan ekstratif.

    Kandidat = fakta yang umurnya > ``max_age_days`` ATAU confidence-nya <
    ``min_confidence``. Dilewati: fakta kedaluwarsa, fakta yang sudah
    digulung, dan record ringkasan (``kind="rollup"``). Kandidat dikelompokkan
    per (``kind``, ``source``); tiap grup beranggota >= ``MIN_GROUP_SIZE``
    menghasilkan SATU ringkasan deterministik zero-token (tanpa LLM, tanpa
    network) yang ditambahkan kembali sebagai record ``kind="rollup"``.

    Fakta asal TIDAK dihapus/diubah — hanya ditandai di file sidecar, tetap
    terbaca penuh dan bisa diaudit. Idempoten: pemanggilan kedua melaporkan
    ``nothing_to_do`` tanpa membuat ringkasan ganda.

    ``dry_run=True`` menghitung semuanya tanpa menulis apa pun (file memory
    dan sidecar tidak disentuh).
    """
    return _rollup_impl(
        identity,
        summarize_body=_extractive_body,
        max_age_days=max_age_days,
        min_confidence=min_confidence,
        dry_run=dry_run,
    )


def rollup_llm(
    identity: str,
    *,
    use_llm: bool = False,
    summarizer: Summarizer | None = None,
    max_age_days: float = 90,
    min_confidence: float = 0.5,
    dry_run: bool = False,
) -> RollupReport | None:
    """Varian rollup dengan peringkas LLM — OPT-IN eksplisit, default MATI TOTAL.

    Tanpa ``use_llm=True`` fungsi ini mengembalikan ``None`` dan TIDAK
    melakukan apa pun: tidak ada network call, tidak ada model call, tidak
    ada perubahan file. Dengan ``use_llm=True`` pun operator WAJIB menyerahkan
    callable ``summarizer(kind, source, facts) -> str``; tidak ada model
    default, dan ``ValueError`` bila tidak disediakan. Seleksi kandidat,
    pengelompokan, provenance (``source_fact_ids``), penandaan sidecar,
    idempotensi, dan aturan anti-pencucian (``rollup-sync``) IDENTIK dengan
    ``rollup()`` — yang diganti hanya cara badan ringkasan dibuat.

    Trade-off biaya token (alasan default OFF):

    - **Biaya.** Setiap grup = 1 request. Estimasi kasar token input ~ jumlah
      karakter fakta / 4; mis. 50 fakta @ ~120 karakter -> ~1.500 token input
      per grup, plus token output ringkasan. Pada memory besar dengan banyak
      grup, satu pemanggilan bisa menghabiskan ribuan token — jalur ekstratif
      ``rollup()`` menghabiskan NOL token untuk hasil yang deterministik.
    - **Determinisme & idempotensi.** Output LLM tidak deterministik: fakta
      yang sama bisa menghasilkan teks ringkasan berbeda antar run (id
      ringkasan tetap stabil karena dihitung dari hash fakta, bukan teks
      ringkasan — jadi tidak ada duplikasi, tetapi audit menjadi kurang
      dapat diulang).
    - **Keamanan.** Fakta ``*-sync`` adalah data pihak ketiga yang tidak
      tepercaya dan bisa membawa instruksi injeksi; mengirimnya ke model
      membuka permukaan prompt-injection yang tidak ada pada jalur ekstratif
      (yang memperlakukan teks murni sebagai data).
    - **Kegagalan.** Bila ``summarizer`` raise untuk sebuah grup, grup itu
      dilewati secara fail-closed (fakta tidak ditandai, tercatat di
      ``report.errors``) dan grup lain tetap diproses.

    Pakai jalur ini hanya bila kualitas ringkasan abstrak lebih penting
    daripada determinisme/nol-biaya — mis. dijalankan manual sesekali oleh
    operator, bukan terjadwal.
    """
    if not use_llm:
        return None
    if summarizer is None or not callable(summarizer):
        raise ValueError(
            "rollup_llm membutuhkan callable `summarizer(kind, source, facts) -> str` "
            "yang diserahkan eksplisit oleh operator. Tidak ada model default — "
            "modul ini tidak memanggil model apa pun atas inisiatif sendiri."
        )
    return _rollup_impl(
        identity,
        summarize_body=summarizer,
        max_age_days=max_age_days,
        min_confidence=min_confidence,
        dry_run=dry_run,
    )


def list_rollups(identity: str) -> list[dict[str, Any]]:
    """Daftar semua ringkasan milik identity untuk audit.

    Tiap entri: ``rollup_id``, ``created_at``, ``group_kind``,
    ``group_source``, ``summary_source``, ``untrusted``, ``fact_count``,
    ``source_fact_ids`` (id fakta asal: hash teks + pembeda source &
    created_at), ``confidence``,
    dan ``text`` (teks ringkasan penuh). Diurut dari yang terlama.
    Mengembalikan [] bila belum ada rollup (atau sidecar korup — kasus korup
    dilaporkan eksplisit oleh ``rollup()`` via ``report.errors``).
    """
    lock = _memory._lock_for(_memory._key(identity))
    with lock:
        sidecar, _error = _read_sidecar(identity)
    if sidecar is None:
        return []
    entries = sorted(
        sidecar.get("rollups", []), key=lambda e: float(e.get("created_at", 0.0))
    )
    return [dict(entry) for entry in entries]


def unroll(identity: str, rollup_id: str) -> dict[str, Any]:
    """Batalkan satu rollup: hapus ringkasannya, buka penanda fakta asalnya.

    - Record ringkasan (``kind="rollup"`` dengan hash teks yang cocok)
      dihapus dari file memory — dipindah ke trash (``reason="unroll"``),
      bukan dibuang permanen, supaya tetap bisa direstorasi.
    - Penanda "sudah digulung" untuk fakta asal dihapus dari sidecar, sehingga
      ``rollup()`` berikutnya bisa menggulung mereka lagi bila masih memenuhi
      syarat.
    - Fakta asal sendiri tidak disentuh: mereka tidak pernah dihapus saat
      rollup, jadi tidak ada yang perlu "dikembalikan" selain penandanya.
    - Bila fakta asal sudah tidak ada di file (dihapus operator setelah
      rollup), id-nya dilaporkan di ``missing_source_facts`` — tidak ada
      yang gagal diam-diam. Entri sidecar lama (id teks-saja) yang HANYA
      cocok via hash legacy — mis. fakta berteks identik dari source
      berbeda masih hidup — dilaporkan terpisah di
      ``legacy_matched_source_facts`` (kecocokan tidak pasti), bukan
      disamarkan sebagai "hadir".

    Mengembalikan dict laporan; ``found=False`` bila ``rollup_id`` tidak
    dikenal (idempoten: unroll dua kali aman — yang kedua melaporkan
    ``found=False``).
    """
    lock = _memory._lock_for(_memory._key(identity))
    with lock:
        sidecar, sidecar_error = _read_sidecar(identity)
        if sidecar_error is not None:
            return {
                "identity": identity,
                "rollup_id": rollup_id,
                "found": False,
                "error": sidecar_error,
            }
        assert sidecar is not None
        entry = next(
            (
                item
                for item in sidecar.get("rollups", [])
                if item.get("rollup_id") == rollup_id
            ),
            None,
        )
        if entry is None:
            return {"identity": identity, "rollup_id": rollup_id, "found": False}

        now = time.time()
        path = _memory._path(identity)
        records = _memory._read(path)
        target_hash = entry.get("summary_hash")
        matched = [
            record
            for record in records
            if record.get("kind") == ROLLUP_KIND
            and fact_id(record["text"]) == target_hash
        ]
        kept = [
            record
            for record in records
            if not (
                record.get("kind") == ROLLUP_KIND
                and fact_id(record["text"]) == target_hash
            )
        ]
        if matched:
            # Ringkasan adalah tulisan agen — amankan ke trash seperti
            # remove()/consolidate(): tidak ada penghapusan permanen.
            _memory._append_trash(
                identity,
                [
                    {"record": record, "deleted_at": now, "reason": "unroll"}
                    for record in matched
                ],
            )
            _memory._write(path, kept)

        rolled_map: dict[str, str] = sidecar.get("rolled_fact_ids", {})
        unmarked = 0
        for fid in entry.get("source_fact_ids", []):
            if rolled_map.get(fid) == rollup_id:
                del rolled_map[fid]
                unmarked += 1
        # Fakta asal yang benar-benar hilang dari file (mis. di-remove
        # operator setelah rollup) dilaporkan eksplisit — audit jujur.
        # Bedakan kecocokan exact vs legacy-only: entri sidecar lama
        # menyimpan id teks-saja, sehingga fakta berteks IDENTIK dari source
        # berbeda yang masih hidup TIDAK BOLEH menyamarkan hilangnya fakta
        # asal yang sebenarnya (itu false-negative missing_source_facts).
        # Entri yang HANYA cocok via hash legacy dilaporkan terpisah di
        # legacy_matched_source_facts — kecocokan tidak pasti, operator
        # memutuskan manual.
        present_exact = {_source_fact_id(record) for record in kept}
        present_legacy = {_legacy_fact_id(record) for record in kept}
        missing: list[str] = []
        legacy_matched: list[str] = []
        for fid in entry.get("source_fact_ids", []):
            if fid in present_exact:
                continue
            if fid in present_legacy:
                legacy_matched.append(fid)
            else:
                missing.append(fid)
        sidecar["rollups"] = [
            item
            for item in sidecar.get("rollups", [])
            if item.get("rollup_id") != rollup_id
        ]
        _write_sidecar(identity, sidecar)
        return {
            "identity": identity,
            "rollup_id": rollup_id,
            "found": True,
            "summary_removed": len(matched),
            "summary_trashed": bool(matched),
            "facts_unmarked": unmarked,
            "missing_source_facts": missing,
            "legacy_matched_source_facts": legacy_matched,
        }
