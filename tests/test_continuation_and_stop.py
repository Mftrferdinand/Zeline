"""Kontinuasi & pembatalan: dua keluhan nyata yang keduanya soal "pesan mana".

1. ``/stop`` membalas TIGA pesan untuk satu pembatalan (konfirmasi + sentinel
   "Stopped." dari turn + "No active task to stop." dari echo). Yang benar satu.
2. "lanjut" mengembalikan topik TERLAMA, bukan yang terakhir dikerjakan, karena
   peringkat archive bm25-first dan "lanjut" dicari sebagai kata kunci.

Semua test di sini offline dan deterministik: archive di-seed langsung dengan
timestamp yang dikontrol, dan DATA_DIR diarahkan ke direktori sementara supaya
``~/.zeline`` milik user tidak pernah tersentuh.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

DAY = 86_400.0


def _fresh_zeline(home: Path):
    """Import ulang paket zeline dengan ``ZELINE_HOME`` menunjuk ke ``home``.

    Patch ``config.DATA_DIR`` SAJA tidak cukup di sini. Test lain (mis.
    ``test_compaction``) membuang semua modul ``zeline.*`` dari ``sys.modules``
    dan mengimpor ulang, jadi modul yang kita pegang di level file bisa BUKAN
    modul yang dipakai kode saat test berjalan — dan modul baru itu membaca
    ``~/.zeline`` yang NYATA. Menyetel env sebelum impor mengikat isolasi ke
    proses, bukan ke objek modul, sehingga urutan test tidak lagi berpengaruh.
    """
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    config = importlib.import_module("zeline.config")
    assert config.DATA_DIR == home, f"isolasi gagal: {config.DATA_DIR}"
    return config


class _ArchiveFixture(unittest.TestCase):
    """Archive terisolasi dengan dua topik: A lama & panjang, B baru & pendek."""

    identity = "telegram:probe"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_home = os.environ.get("ZELINE_HOME")
        self.addCleanup(self._restore_home)
        self.config = _fresh_zeline(Path(self._tmp.name))
        self.session_store = importlib.import_module("zeline.session_store")
        self.store = self.session_store.SessionPersistence()
        self.now = time.time()

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old_home
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def seed(self, role: str, content: str, title: str, ts: float) -> None:
        with closing(sqlite3.connect(str(self.store.path))) as conn, conn:
            conn.execute(
                "INSERT INTO archive (key, role, content, title, ts) VALUES (?,?,?,?,?)",
                (self.session_store._key(self.identity), role, content, title, ts),
            )

    def seed_two_topics(self) -> None:
        # Topik A: dua hari lalu, enam turn, menyebut "lanjut" berulang.
        for index in range(6):
            base = self.now - 2 * DAY + index * 60
            self.seed("user", f"lanjut invoice bulan {index}, lanjutin format tabelnya", "Invoice generator", base)
            self.seed("assistant", f"Invoice {index} dibuat, lanjut ke item berikutnya.", "Invoice generator", base + 30)
        # Topik B: sepuluh menit lalu, dua turn, topik lain.
        self.seed("user", "gateway telegram mati pas kirim foto, benerin", "Gateway telegram foto", self.now - 600)
        self.seed("assistant", "Handler foto di telegram.py: download gagal.", "Gateway telegram foto", self.now - 570)

    def titles(self, rows: list[dict]) -> set[str]:
        return {str(row["title"]) for row in rows}


class ContinuationAnchorTests(_ArchiveFixture):
    def test_last_thread_returns_the_newest_topic_only(self):
        self.seed_two_topics()
        rows = self.store.last_thread(self.identity)
        self.assertEqual(self.titles(rows), {"Gateway telegram foto"})
        self.assertEqual(len(rows), 2)

    def test_last_thread_is_chronological_so_the_anchor_reads_in_order(self):
        self.seed_two_topics()
        rows = self.store.last_thread(self.identity)
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[-1]["role"], "assistant")

    def test_last_thread_does_not_split_on_a_title_that_names_no_topic(self):
        """``New Session``/kosong bukan pembatas topik.

        Turn tanpa judul tetap boleh bergabung dengan turn sebelumnya — SELAMA
        keduanya masih dalam satu rentang waktu sesi. Jeda waktu tetap pembatas
        utama, jadi seed di sini dibuat berdekatan.
        """
        self.seed("user", "topik lama", "Sesuatu", self.now - 900)
        self.seed("user", "pesan tanpa judul", "New Session", self.now - 60)
        rows = self.store.last_thread(self.identity, limit=5)
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[-1]["content"], "pesan tanpa judul")

    def test_last_thread_stops_at_a_session_gap_even_when_titles_match(self):
        """Jeda waktu adalah pembatas utama, bukan title.

        ``session.title`` diambil dari pesan pertama sesi, jadi dua sesi yang
        dibuka dengan kata yang sama ("lanjut") masuk bucket title yang sama.
        Tanpa pembatas waktu, "lanjut" hari ini menyeret kerjaan kemarin.
        """
        self.seed("user", "kerjaan kemarin", "lanjut", self.now - DAY)
        self.seed("assistant", "hasil kemarin", "lanjut", self.now - DAY + 60)
        self.seed("user", "kerjaan sekarang", "lanjut", self.now - 300)
        self.seed("assistant", "hasil sekarang", "lanjut", self.now - 240)
        rows = self.store.last_thread(self.identity, limit=12)
        contents = [r["content"] for r in rows]
        self.assertEqual(contents, ["kerjaan sekarang", "hasil sekarang"])
        self.assertNotIn("kerjaan kemarin", contents)

    def test_last_thread_returns_nothing_when_the_newest_turn_is_stale(self):
        """``stale_after``: tidak ada kerja RECENT → jangan sodorkan sesi lama.

        ``append_turn`` jalan setelah reply, jadi saat user mengetik "lanjut"
        di sesi baru, baris terbaru archive masih milik sesi sebelumnya.
        """
        self.seed("user", "kerjaan semalam", "hy", self.now - 8 * 3600)
        self.seed("assistant", "hasil semalam", "hy", self.now - 8 * 3600 + 60)
        self.assertEqual(
            self.store.last_thread(self.identity, stale_after=6 * 3600), []
        )
        # Tanpa ambang, perilaku lama tetap tersedia untuk pemanggil lain.
        self.assertTrue(self.store.last_thread(self.identity))

    def test_last_thread_on_empty_archive_is_empty_not_an_error(self):
        self.assertEqual(self.store.last_thread(self.identity), [])

    def test_search_ranking_prefers_the_recent_topic_when_relevance_ties(self):
        """Regresi utama: dulu topik lama yang panjang selalu menang."""
        self.seed_two_topics()
        rows = self.store.search_archive(self.identity, "telegram invoice")
        self.assertEqual(rows[0]["title"], "Gateway telegram foto")

    def test_search_still_finds_an_old_topic_when_the_query_names_it(self):
        self.seed_two_topics()
        rows = self.store.search_archive(self.identity, "invoice tabel")
        self.assertEqual(self.titles(rows), {"Invoice generator"})

    def test_search_with_an_empty_query_returns_nothing(self):
        self.seed_two_topics()
        self.assertEqual(self.store.search_archive(self.identity, "   "), [])

    def test_archive_stays_isolated_per_identity(self):
        self.seed_two_topics()
        self.assertEqual(self.store.last_thread("telegram:someone-else"), [])


class ContinuationQueryClassifierTests(unittest.TestCase):
    """Query mana yang berarti "yang terakhir" dan mana yang menyebut topik."""

    def setUp(self) -> None:
        self.tools = importlib.import_module("zeline.tools")

    def test_bare_continuations_carry_no_topic(self):
        for query in ("lanjut", "lanjutin", "LANJUT OY", "terusin dong", "gas", "oy",
                      "yang tadi", "lanjutin yang tadi", "continue", "resume",
                      "kerjain sisanya", "what were we doing", ""):
            with self.subTest(query=query):
                self.assertTrue(self.tools._is_continuation_query(query))

    def test_a_query_that_names_a_topic_is_still_a_keyword_search(self):
        for query in ("lanjut invoice", "terusin gateway telegram", "file config.py",
                      "xauusd analysis", "yang tadi soal postgres"):
            with self.subTest(query=query):
                self.assertFalse(self.tools._is_continuation_query(query))


class RecallHistoryToolTests(_ArchiveFixture):
    """Tool ``recall_history`` harus MEMILIH jalur yang benar, bukan cuma punya keduanya."""

    def _executor(self):
        tools = importlib.import_module("zeline.tools")
        return tools.ToolExecutor(identity=self.identity, profile="safe", workspace=self._tmp.name)

    def test_bare_lanjut_answers_with_the_newest_thread(self):
        self.seed_two_topics()
        out = self._executor()._recall_history("lanjut")
        self.assertIn("MOST RECENT thread", out)
        self.assertIn("gateway telegram mati", out)
        self.assertNotIn("invoice bulan", out)

    def test_empty_query_answers_with_the_newest_thread_too(self):
        self.seed_two_topics()
        out = self._executor()._recall_history("")
        self.assertIn("gateway telegram mati", out)
        self.assertNotIn("invoice bulan", out)

    def test_a_named_topic_still_reaches_the_old_conversation(self):
        self.seed_two_topics()
        out = self._executor()._recall_history("invoice tabel")
        self.assertIn("invoice bulan", out)

    def test_empty_archive_says_so_instead_of_failing(self):
        out = self._executor()._recall_history("lanjut")
        self.assertIn("No recent work to continue", out)

    def test_stale_archive_tells_the_model_to_ask_not_to_guess(self):
        """Regresi nyata: "lanjut" pagi ini me-recall pekerjaan semalam.

        ``append_turn`` jalan SETELAH reply, jadi saat user membuka sesi baru
        dan bilang "lanjut", baris terbaru archive masih milik sesi sebelumnya.
        Jalur kontinuasi tidak boleh menyodorkan itu sebagai konteks aktif —
        dan tidak boleh jatuh ke ``recent_archive`` yang mengabaikan batas sesi.
        """
        old = self.now - 8 * 3600
        self.seed("user", "bikin veo-chat fastapi", "hy", old)
        self.seed("assistant", "veo-chat: pip lambat, install manual", "hy", old + 60)
        out = self._executor()._recall_history("lanjut")
        self.assertIn("No recent work to continue", out)
        self.assertIn("Ask the user", out)
        # Yang paling penting: transkrip sesi lama TIDAK ikut terbawa.
        self.assertNotIn("veo-chat", out)


class StopRepliesOnceTests(unittest.TestCase):
    """Satu ``/stop`` = satu pesan. Tidak ada sentinel, tidak ada echo, tidak ada refleksi."""

    def setUp(self) -> None:
        self.telegram = importlib.import_module("zeline.gateways.telegram")
        # State modul global: bersihkan supaya test tidak saling mewarisi jendela stop.
        with self.telegram._recent_stops_lock:
            self.telegram._recent_stops.clear()
        self.addCleanup(self._clear_stops)

    def _clear_stops(self) -> None:
        with self.telegram._recent_stops_lock:
            self.telegram._recent_stops.clear()

    def _sessions(self, *, running: bool):
        class Sessions:
            stopped = None

            def status(self, _identity):
                return {"title": "Bangun aplikasi", "agent_running": running}

            def stop(self, identity):
                self.stopped = identity
                return running

        return Sessions()

    def _stop(self, sessions):
        with mock.patch.object(self.telegram, "_api_call") as api:
            handled = self.telegram._handle_command_update(
                "bot-api", "/stop", sessions, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        return handled, api

    def test_first_stop_sends_exactly_one_confirmation(self):
        handled, api = self._stop(self._sessions(running=True))
        self.assertTrue(handled)
        self.assertEqual(api.call_count, 1)
        text = api.call_args.kwargs["text"]
        self.assertIn("❄️ Stopped — Bangun aplikasi", text)
        self.assertIn("force-killed", text)

    def test_second_stop_right_after_is_silent_not_no_active_task(self):
        """Ini pesan ketiga yang dikeluhkan: /stop dobel bilang 'No active task'."""
        self._stop(self._sessions(running=True))
        handled, api = self._stop(self._sessions(running=False))
        self.assertTrue(handled)
        self.assertEqual(api.call_count, 0, "pembatalan yang sudah dikonfirmasi tidak boleh dibalas lagi")

    def test_stop_when_genuinely_idle_still_says_so(self):
        handled, api = self._stop(self._sessions(running=False))
        self.assertTrue(handled)
        self.assertEqual(api.call_args.kwargs["text"], "No active task to stop.")

    def test_the_echo_window_expires_so_a_later_stop_is_answered(self):
        self._stop(self._sessions(running=True))
        with mock.patch.object(self.telegram.time, "monotonic",
                               return_value=time.monotonic() + self.telegram._STOP_ECHO_SECONDS + 1):
            _, api = self._stop(self._sessions(running=False))
        self.assertEqual(api.call_args.kwargs["text"], "No active task to stop.")

    def test_cancelled_turn_sends_no_reply_and_skips_reflection(self):
        """Sentinel "Stopped." dari turn yang dibatalkan tidak boleh jadi pesan kedua."""
        agent = importlib.import_module("zeline.agent")

        class Sessions:
            reflected = False

            def send(self, **_kwargs):
                return agent.CANCELLED_REPLY

            def reflect(self, _identity):
                self.reflected = True
                return "seharusnya tidak pernah terkirim"

        sessions = Sessions()
        self.telegram._note_stop("telegram:42")
        with mock.patch.object(self.telegram, "_api_call") as api, \
                mock.patch.object(self.telegram, "_LiveStatus") as live, \
                mock.patch.object(self.telegram, "_start_working_heartbeat") as heartbeat:
            heartbeat.return_value = mock.Mock()
            self.telegram._send_agent_reply(
                "bot-api", sessions, chat_id=42, identity="telegram:42",
                text="kerjakan sesuatu", tool_profile="safe",
            )
            live.return_value.clear.assert_called()
        sent = [c for c in api.call_args_list if c.args[1:2] == ("sendMessage",)]
        self.assertEqual(sent, [], "turn yang dibatalkan tidak boleh mengirim pesan apa pun")
        self.assertFalse(sessions.reflected, "refleksi tidak boleh jalan untuk turn yang dibatalkan")

    def test_a_normal_reply_is_unaffected_by_the_stop_window(self):
        """Jendela stop tidak boleh menelan jawaban asli yang kebetulan sesudahnya."""
        class Sessions:
            def send(self, **_kwargs):
                return "Ini jawaban normal."

            def reflect(self, _identity):
                return None

        self.telegram._note_stop("telegram:42")
        with mock.patch.object(self.telegram, "_api_call") as api, \
                mock.patch.object(self.telegram, "_LiveStatus"), \
                mock.patch.object(self.telegram, "_start_working_heartbeat") as heartbeat:
            heartbeat.return_value = mock.Mock()
            self.telegram._send_agent_reply(
                "bot-api", Sessions(), chat_id=42, identity="telegram:42",
                text="tanya biasa", tool_profile="safe",
            )
        texts = [c.kwargs.get("text", "") for c in api.call_args_list]
        self.assertTrue(any("Ini jawaban normal." in t for t in texts))


class CompactionDigestFramingTests(unittest.TestCase):
    """Digest compaction harus terbaca sebagai CATATAN, bukan perintah baru."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_home = os.environ.get("ZELINE_HOME")
        self.addCleanup(self._restore_home)
        _fresh_zeline(Path(self._tmp.name))
        self.compaction = importlib.import_module("zeline.compaction")

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old_home
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def _digest(self, asks: list[str]) -> str:
        messages = [{"role": "user", "content": ask} for ask in asks]
        return self.compaction.digest(messages, None)

    def test_digest_states_it_is_not_a_request(self):
        text = self._digest(["bikin invoice", "benerin gateway"])
        self.assertIn("NOT A REQUEST", text)
        self.assertIn("already happened", text)

    def test_digest_names_the_most_recent_ask_explicitly(self):
        text = self._digest(["topik paling lama", "topik tengah", "topik paling baru"])
        self.assertIn('the last bullet: "topik paling baru"', text)
        self.assertLess(text.index("topik paling lama"), text.index("topik paling baru"))

    def test_digest_still_respects_its_size_bound(self):
        text = self._digest([f"permintaan {i} " + "x" * 500 for i in range(40)])
        self.assertLessEqual(len(text), self.compaction.MAX_DIGEST_CHARS + 100)


if __name__ == "__main__":
    unittest.main()
