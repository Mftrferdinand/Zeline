"""Proactive briefing: ringkasan inisiatif agen yang hanya dikirim bila layak.

Cron job briefing berjalan sebagai identity chat user itu sendiri (bukan
``cron:<id>``) supaya bisa membaca memory, task, dan riwayat chat tersebut.
Agent turn-nya diinstruksikan membalas EXACTLY ``__SILENT__`` bila tidak ada
yang benar-benar baru/penting — ``scheduler.deliver()`` mengenali penanda itu
dan tidak mengirim apa pun (anti-spam).
"""

from __future__ import annotations

import re

from zeline import scheduler

#: Penanda "tidak ada yang layak dikirim", dikenali scheduler.deliver().
SILENT = "__SILENT__"

#: Prefix id untuk semua job briefing, supaya mudah dicari/dihapus.
_ID_PREFIX = "briefing-"


def _slug(chat: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (chat or "").strip()).strip("-").lower()
    return slug or "chat"


def _job_id(chat: str) -> str:
    return f"{_ID_PREFIX}{_slug(chat)}"


def briefing_prompt(identity: str) -> str:
    """Prompt untuk agent turn briefing proaktif.

    ``identity`` hanya dipakai sebagai konteks (siapa yang di-briefing);
    turn-nya sendiri berjalan sebagai identity tersebut via ``run_as``.
    """
    return (
        "Kamu sedang menjalankan PROACTIVE BRIEFING — ringkasan inisiatif untuk "
        f"user ({identity}), bukan jawaban atas pertanyaan.\n\n"
        "Langkah:\n"
        "1. Baca tool `list_memory` untuk fakta dan preferensi user.\n"
        "2. Lihat daftar task terbuka (sudah disuntikkan di system prompt kamu).\n"
        "3. Baca tool `recall_history` dengan query kosong untuk melihat "
        "obrolan terakhir user ini.\n\n"
        "Susun DIGEST yang singkat dan padat, dalam bahasa yang biasa dipakai user:\n"
        "- maksimal 5 baris/poin, tanpa basa-basi pembuka;\n"
        "- hanya hal yang benar-benar baru, penting, atau butuh tindak lanjut "
        "(deadline dekat, task yang mangkrak, info dari memory yang relevan hari ini);\n"
        "- JANGAN mengulang hal yang sudah user ketahui atau yang sudah dibahas tuntas.\n\n"
        "Aturan anti-spam (PENTING):\n"
        "- Kalau setelah membaca semuanya tidak ada yang benar-benar baru, penting, "
        "atau layak disampaikan, balas EXACTLY `__SILENT__` — tanpa teks lain, "
        "tanpa penjelasan, tanpa permintaan maaf.\n"
        "- Lebih baik diam daripada mengirim ringkasan basa-basi."
    )


def preview(chat: str) -> str:
    """Kembalikan prompt briefing tanpa membuat job apa pun."""
    return briefing_prompt((chat or "").strip())


def enable(chat: str, time: str = "07:00") -> scheduler.Job:
    """Aktifkan briefing harian untuk satu chat.

    Membuat (atau mengganti) cron job ``briefing-<slug>`` dengan schedule
    harian ``<time>`` (format ``HH:MM``, mis. ``"07:00"``) yang berjalan sebagai
    identity chat itu sendiri dan mengirim hasilnya ke chat tersebut.
    Format chat: ``telegram:<chat_id>``.
    """
    chat = (chat or "").strip()
    if not chat:
        raise ValueError("chat target kosong — pakai format 'telegram:<chat_id>'")
    job_id = _job_id(chat)
    # Ganti job lama bila sudah ada, supaya tidak dobel.
    scheduler.remove_job(job_id)
    job = scheduler.add_job(
        time,
        briefing_prompt(chat),
        deliver=chat,
        run_as=chat,
    )
    # add_job memakai auto-id (job1, job2, ...): rename ke id briefing.
    # Fungsi _read_jobs/_write_jobs dipakai langsung karena ini satu paket
    # dengan scheduler; _JOBS_LOCK adalah RLock jadi aman dipakai bersarang.
    with scheduler._JOBS_LOCK:
        jobs = scheduler._read_jobs()
        for existing in jobs:
            if existing.id == job.id:
                existing.id = job_id
                break
        scheduler._write_jobs(jobs)
    return scheduler.find_job(job_id) or job


def disable() -> bool:
    """Hapus semua job briefing. Kembalikan True bila ada yang dihapus."""
    removed = False
    for job in scheduler.list_jobs():
        if job.id.startswith(_ID_PREFIX) and scheduler.remove_job(job.id):
            removed = True
    return removed


def status() -> str:
    """Ringkasan job briefing yang sedang terdaftar."""
    jobs = [job for job in scheduler.list_jobs() if job.id.startswith(_ID_PREFIX)]
    if not jobs:
        return "Tidak ada proactive briefing yang aktif."
    lines = []
    for job in jobs:
        state = "aktif" if job.enabled else "jeda"
        lines.append(
            f"- {job.id}: {job.parsed().describe()}, {state}, "
            f"kirim ke {job.deliver}, jalan sebagai {job.run_as or 'cron:' + job.id}"
        )
    return "Proactive briefing:\n" + "\n".join(lines)
