"""Session-scoped approval cache untuk risk gate approval (tanpa opsi izin permanen).

Kenapa cache ini hanya seumur SESI, bukan selamanya:

- "Allow selamanya" dari chat adalah footgun klasik: satu tap santai hari
  ini menjadi izin permanen untuk perintah destruktif di masa depan, lama
  setelah operator lupa pernah mengizinkannya. Keputusan desain ini menghapus opsi itu
  dari picker — izin permanen hanya bisa lewat edit file config yang disengaja
  (deliberate friction), bukan dari percakapan.
- Scope sesi adalah kompromi yang dipilih: operator yang sedang mengerjakan
  satu alur (mis. serangkaian ``run_shell`` saat debug) tidak ditanya
  berulang-ulang, tapi izin itu mati begitu sesi berakhir (``/new``,
  ``/reset``, eviction) — jadi tidak ada izin basi yang bertahan diam-diam.
  ``/stop`` juga mencabut izin: itu interupsi eksplisit di tengah sesi, jadi
  turn berikutnya dinilai ulang dari awal, bukan melanjutkan atas izin yang
  diberikan sebelum interupsi.

Kenapa cache in-memory (bukan di disk):

- Fail-closed saat restart abnormal: kalau proses gateway mati, seluruh izin
  ikut hilang dan operator ditanya ulang. Izin yang *survive* crash adalah
  izin yang paling berbahaya.
- Key = ``(identity, nama tool, extra)``. Kelas risiko melekat pada definisi tool
  (satu tool = satu kelas risiko), jadi key tool-name sudah mencakup kelas
  risikonya. ``extra`` kosong kecuali untuk ``spawn_worker``, yang izin
  sesinya dicatat per deklarasi grants yang disetujui operator — "Allow
  sesi ini" untuk satu deklarasi grants tidak berlaku untuk deklarasi
  yang berbeda. Identity penuh dipakai apa adanya — ``cron:<id>`` tidak pernah
  berbagi cache dengan ``telegram:<chat>``, dan sub-agent (``::sub``) tidak
  berbagi dengan induknya.

Satu-satunya parser jawaban approval yang sah ada di sini (``parse_verdict``): kontrak
dengan worker lain — string yang didukung ``"allow"``/``"allow once"`` =
sekali, ``"allow_session"``/``"allow sesi ini"`` = cache sesi, selain itu =
deny. Semua pemanggil (agent loop, cron, refleksi) WAJIB lewat fungsi ini
supaya satu choke point approval.
"""

from __future__ import annotations

import threading

#: Opsi picker approval. Urutan = urutan tampil; "Allow
#: once" pertama = pilihan paling aman = default.
APPROVAL_OPTIONS: tuple[str, ...] = ("Allow once", "Allow sesi ini", "Deny")

#: Hasil ``parse_verdict``: "once" | "session" | "deny".
_ONCE = frozenset({"allow", "allow once"})
_SESSION = frozenset({"allow_session", "allow sesi ini"})

#: Cache key = ``(identity, nama tool, extra)``. ``extra`` kosong untuk semua
#: tool biasa. Satu-satunya tool yang memakainya adalah ``spawn_worker``:
#: izin sesi untuk spawn dicatat per deklarasi grants yang disetujui
#: (lihat ``zeline.tools._spawn_grants_key``), sehingga "Allow sesi ini"
#: untuk spawn read-only TIDAK PERNAH berlaku untuk spawn berikutnya yang
#: meminta grants lebih luas — deklarasi yang berbeda ditanya ulang.
_ALLows: dict[tuple[str, str, str], None] = {}
_LOCK = threading.Lock()


def parse_verdict(verdict: object) -> str:
    """Normalisasi jawaban operator menjadi keputusan approval.

    Toleran: case-insensitive, whitespace diabaikan, string error/timeout
    dari ``interaction.ask`` (``"NO ANSWER: ..."``, ``"CANCELLED: ..."``)
    otomatis jatuh ke "deny" — aman secara default.
    """
    normalized = str(verdict or "").strip().lower()
    if normalized in _ONCE:
        return "once"
    if normalized in _SESSION:
        return "session"
    return "deny"


def session_allowed(identity: str, tool: str, extra: str = "") -> bool:
    """True bila tool ini sudah di-allow untuk sesi ini (tanpa tanya lagi).

    ``extra`` membedakan izin sesi per konteks pemanggilan — dipakai hanya
    oleh ``spawn_worker`` untuk deklarasi grants (string kosong = perilaku
    lama untuk semua tool lain).
    """
    with _LOCK:
        return (identity or "cli:local", tool, extra) in _ALLows


def grant_session_allow(identity: str, tool: str, extra: str = "") -> None:
    """Catat izin sesi untuk (identity, tool, extra) setelah operator memilih
    "Allow sesi ini"."""
    with _LOCK:
        _ALLows[(identity or "cli:local", tool, extra)] = None


def clear_session_allows(identity: str) -> int:
    """Hapus SEMUA izin sesi milik satu identity. Dipanggil saat sesi
    berakhir (``SessionStore.reset``/``stop``, eviction). Mengembalikan
    jumlah izin yang dibersihkan (berguna untuk audit/test)."""
    key = identity or "cli:local"
    with _LOCK:
        doomed = [entry for entry in _ALLows if entry[0] == key]
        for entry in doomed:
            del _ALLows[entry]
        return len(doomed)
