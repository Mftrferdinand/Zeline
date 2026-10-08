"""Gateway WebChat UI Zeline — dashboard web minimal.

Satu file ini berisi server HTTP (stdlib ``ThreadingHTTPServer``) sekaligus UI
chat satu halaman (HTML/CSS/JS inline, TANPA dependensi eksternal/CDN — jalan
offline penuh).

Endpoint:

- ``GET /health`` → status tanpa rahasia (tanpa auth, seperti webhook)
- ``GET /`` → JIKA terautentikasi: dashboard penuh (tab Ringkasan, Chat,
  Workers, Goals, Workflows, Skills, Cron, Connectors); JIKA tidak: halaman
  login token SAJA (tidak ada data sensitif, tidak ada panel).
- ``POST /api/message`` → JSON ``{"chat_id":"abc", "text":"halo"}``
  (auth wajib) → ``{"reply": ...}``. Identitas sesi: ``webchat:<chat_id>``.
- ``GET /api/status`` → (auth wajib) JSON ringkas: model aktif, worker yang
  berjalan, dan token usage hari ini. Tidak pernah memuat token/key.

Autentikasi setiap endpoint baca/tulis memakai salah satu (seperti webhook):

- ``Authorization: Bearer <webchat-token>``
- ``X-Zeline-Token: <webchat-token>``

Konfigurasi (``gateways.webchat`` di config):

- ``token``: wajib, minimum 16 karakter ASCII saja (non-ASCII ditolak di
  validasi — perbandingan hmac tidak konsisten antar encoding). Generate
  via ``zeline gateway setup webchat`` (token hanya ditampilkan saat setup).
- ``host``: default ``127.0.0.1`` (bind lokal saja).
- ``port``: default ``8787`` (berbeda dari webhook 8765 agar tidak bentrok).
- ``tool_profile``: HARUS ``"safe"`` — ditolak di validasi bila diisi lain.

Cara mengekspos dengan aman (bila butuh akses dari luar mesin):

1. Jangan ubah ``host`` ke ``0.0.0.0`` tanpa reverse proxy di depannya.
2. Letakkan reverse proxy HTTPS (nginx/Caddy/tunnel) di depan port ini,
   proxy_pass ke ``127.0.0.1:8787``.
3. Gunakan token kuat (>= 32 karakter acak) dan jaga kerahasiaannya;
   siapapun yang memegang token dapat mengobrol dengan Zeline.

Batasan:

- WebChat memakai SATU bearer token tanpa identitas per-pengguna (sama
  seperti webhook), sehingga tidak bisa membuktikan identitas owner.
  Karena itu gateway ini **safe-only**: ``tool_profile`` di atas ``"safe"``
  ditolak oleh ``zeline.gateways._validate_tool_policy`` maupun
  ``validate_config`` di sini. Untuk tool elevated gunakan gateway
  messaging dengan owner allowlist (mis. telegram).
- UI memakai ``sessionStorage`` untuk menyimpan token di browser dan TIDAK
  memakai cookie, sehingga tidak ada permukaan CSRF. Semua teks
  user/balasan dirender via ``textContent`` (tidak pernah ``innerHTML``)
  agar balasan berisi ``<script>`` tidak menjadi XSS.
- ``POST /api/message`` dibatasi 30 permintaan per 60 detik per IP
  (sliding window in-memory); selebihnya dijawab ``429`` tanpa menjalankan
  turn agen — satu klien tidak bisa membanjiri endpoint.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from zeline import interaction
from zeline.agent import ZelineError

from .webhook import MAX_BODY_BYTES

DEFAULT_PORT = 8787

# ---------------------------------------------------------------------------
# Rate limiting POST per IP (sliding window in-memory)
# ---------------------------------------------------------------------------

#: Batas POST /api/message: 30 permintaan per 60 detik per IP. Tanpa ini satu
#: klien bisa membanjiri endpoint (setiap POST menjalankan satu turn agen).
_POST_RATE_MAX = 30
_POST_RATE_WINDOW_S = 60.0
#: Batas jumlah IP berbeda yang dilacak limiter. Tanpa ini, penyapu IP yang
#: sekali-sentuh membuat tabel _hits tumbuh tanpa batas — key yang tidak
#: pernah disentuh lagi tidak pernah disapu, sehingga klaim "memori
#: terbatas" di docstring kelas di bawah tidak terpenuhi.
_POST_RATE_MAX_KEYS = 4096


class _SlidingWindowLimiter:
    """Sliding-window rate limiter in-memory, aman untuk thread.

    Kunci = alamat IP klien (``self.client_address[0]``). Riwayat timestamp
    per IP dipangkas tiap pemeriksaan; IP yang tidak aktif dihapus saat
    disentuh lagi. Selain itu jumlah total IP yang dilacak dibatasi
    ``max_keys``: saat key baru tiba dan tabel penuh, key dengan aktivitas
    TERLAMA dibuang (evict oldest) — banjir IP unik tidak bisa
    membesar-besarkan tabel tanpa batas. Scope: satu proses server ini saja.
    """

    def __init__(
        self,
        max_hits: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = _POST_RATE_MAX_KEYS,
    ) -> None:
        self._max_hits = max(1, int(max_hits))
        self._window_s = float(window_s)
        self._clock = clock
        self._max_keys = max(1, int(max_keys))
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """True bila permintaan dari ``key`` boleh lanjut (dan dicatat)."""
        now = self._clock()
        cutoff = now - self._window_s
        with self._lock:
            hits = self._hits.get(key)
            if hits is not None:
                while hits and hits[0] <= cutoff:
                    hits.popleft()
                if not hits:
                    # IP tidak aktif lagi: hapus key-nya agar memori tidak
                    # tumbuh tanpa batas (sesuai klaim docstring kelas).
                    del self._hits[key]
                    hits = None
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    # Tabel penuh dan key BARU tiba: korbankan key yang
                    # aktivitasnya paling lama (bukan key yang baru tiba).
                    self._evict_oldest()
                hits = self._hits[key] = deque()
            if len(hits) >= self._max_hits:
                return False
            hits.append(now)
            return True

    def _evict_oldest(self) -> None:
        """Buang key dengan aktivitas terakhir paling lama.

        Hanya dipanggil dengan ``_lock`` dipegang, saat key baru tiba dan
        tabel sudah penuh. Iterasi baca-saja lalu satu ``del`` — aman
        terhadap mutasi-during-iteration.
        """
        oldest_key: str | None = None
        oldest_ts = float("inf")
        for key, hits in self._hits.items():
            last = hits[-1] if hits else float("-inf")
            if last < oldest_ts:
                oldest_ts = last
                oldest_key = key
        if oldest_key is not None:
            del self._hits[oldest_key]


_POST_LIMITER = _SlidingWindowLimiter(_POST_RATE_MAX, _POST_RATE_WINDOW_S)

# ---------------------------------------------------------------------------
# Halaman login (disajikan TANPA auth — tidak memuat data sensitif / panel chat)
# ---------------------------------------------------------------------------
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zeline WebChat — Login</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#111827;color:#e5e7eb;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.card{background:#1f2937;border-radius:12px;padding:28px;width:min(360px,90vw);
box-shadow:0 8px 32px rgba(0,0,0,.4)}
h1{font-size:20px;margin:0 0 6px}
p{font-size:13px;color:#9ca3af;margin:0 0 16px}
input[type=password]{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;
border:1px solid #374151;background:#111827;color:#e5e7eb;font-size:14px}
button{width:100%;margin-top:12px;padding:10px;border:none;border-radius:8px;
background:#2563eb;color:#fff;font-size:15px;cursor:pointer}
button:disabled{opacity:.5;cursor:wait}
#error{color:#f87171;font-size:13px;min-height:20px;margin-top:8px}
</style>
</head>
<body>
<div class="card">
<h1>Zeline WebChat</h1>
<p>Masukkan token webchat untuk membuka dashboard chat.</p>
<form id="login">
<input type="password" id="token" placeholder="Token webchat" autocomplete="off" autofocus>
<button type="submit" id="btn">Masuk</button>
<div id="error"></div>
</form>
</div>
<script>
"use strict";
var form = document.getElementById("login");
var input = document.getElementById("token");
var btn = document.getElementById("btn");
var err = document.getElementById("error");
form.addEventListener("submit", function (e) {
  e.preventDefault();
  var token = input.value.trim();
  if (!token) { err.textContent = "Token tidak boleh kosong."; return; }
  btn.disabled = true;
  err.textContent = "";
  // Verifikasi token dengan mengambil halaman chat memakai header auth
  // (navigasi browser biasa tidak bisa menyetel header Authorization).
  fetch("/", {headers: {"Authorization": "Bearer " + token}})
    .then(function (resp) {
      if (resp.status === 401) { throw new Error("Token salah."); }
      if (!resp.ok) { throw new Error("Server error (" + resp.status + ")."); }
      return resp.text();
    })
    .then(function (html) {
      try { sessionStorage.setItem("zeline_webchat_token", token); } catch (ex) {}
      document.open();
      document.write(html);
      document.close();
    })
    .catch(function (ex) {
      err.textContent = ex.message || "Gagal masuk.";
      btn.disabled = false;
    });
});
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Halaman chat (hanya disajikan SETELAH auth — berisi panel chat penuh)
# ---------------------------------------------------------------------------
_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zeline Dashboard</title>
<style>
:root{--bg:#111827;--panel:#1f2937;--border:#374151;--text:#e5e7eb;--muted:#9ca3af;
--accent:#2563eb;--green:#10b981;--yellow:#f59e0b;--red:#ef4444}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
margin:0;display:flex;flex-direction:column;height:100vh}
header{display:flex;align-items:center;justify-content:space-between;gap:8px;
padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--border);flex-wrap:wrap}
header h1{font-size:16px;margin:0}
#statusline{font-size:12px;color:var(--muted)}
#logout{background:none;border:1px solid #4b5563;color:var(--text);border-radius:6px;
padding:4px 10px;font-size:12px;cursor:pointer}
nav#tabs{display:flex;gap:4px;overflow-x:auto;background:var(--panel);
border-bottom:1px solid var(--border);padding:6px 8px;-webkit-overflow-scrolling:touch}
nav#tabs button{flex:0 0 auto;background:none;border:none;color:var(--muted);
padding:8px 14px;font-size:13px;border-radius:8px;cursor:pointer;white-space:nowrap}
nav#tabs button.active{background:var(--accent);color:#fff}
nav#tabs button:hover{color:var(--text)}
main{flex:1;overflow-y:auto;padding:16px}
.tabpane{display:none}
.tabpane.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
.card .num{font-size:24px;font-weight:700;margin:0}
.card .lbl{font-size:12px;color:var(--muted);margin:4px 0 0}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--border);
border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:520px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);
vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
.badge.running{background:#065f46;color:#a7f3d0}
.badge.queued{background:#92400e;color:#fde68a}
.badge.done,.badge.completed{background:#1e3a8a;color:#bfdbfe}
.badge.failed{background:#7f1d1d;color:#fecaca}
.badge.active{background:#065f46;color:#a7f3d0}
.badge.available{background:#1e3a8a;color:#bfdbfe}
.badge.paused{background:#4b5563;color:#d1d5db}
.muted{color:var(--muted);font-size:13px}
.progress{height:8px;background:#374151;border-radius:4px;overflow:hidden;min-width:80px}
.progress>div{height:100%;background:var(--accent)}
.goalcard{background:var(--panel);border:1px solid var(--border);border-radius:10px;
padding:14px;margin-bottom:12px}
.goalcard h3{margin:0 0 6px;font-size:15px}
.goalcard .row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px;
color:var(--muted)}
.btn{background:var(--accent);border:none;color:#fff;border-radius:8px;padding:8px 14px;
font-size:13px;cursor:pointer}
.btn.danger{background:var(--red)}
.btn.small{padding:4px 10px;font-size:12px}
.toolbar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
input[type=search]{padding:8px 12px;border-radius:8px;border:1px solid var(--border);
background:var(--bg);color:var(--text);font-size:13px;min-width:200px}
/* chat */
#messages{flex:1;overflow-y:auto;padding:4px 0 12px;display:flex;flex-direction:column;gap:10px;
min-height:200px}
.msg{max-width:78%;padding:10px 14px;border-radius:12px;font-size:14px;line-height:1.5;
white-space:pre-wrap;word-wrap:break-word}
.user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px}
.zeline{align-self:flex-start;background:var(--panel);border:1px solid var(--border);
border-bottom-left-radius:4px}
.system{align-self:center;background:none;color:#6b7280;font-size:12px}
#typing{align-self:flex-start;color:#6b7280;font-size:13px;font-style:italic;display:none}
#composer{display:flex;gap:8px;padding-top:8px}
#input{flex:1;padding:10px;border-radius:8px;border:1px solid var(--border);
background:var(--bg);color:var(--text);font-size:14px;font-family:inherit;resize:none}
#send{padding:10px 20px;border:none;border-radius:8px;background:var(--accent);
color:#fff;font-size:14px;cursor:pointer}
#send:disabled{opacity:.5;cursor:wait}
.detail{background:var(--bg);border:1px solid var(--border);border-radius:8px;
padding:12px;margin-top:8px;font-size:13px;white-space:pre-wrap;word-wrap:break-word}
@media (max-width:600px){
  .cards{grid-template-columns:repeat(2,1fr)}
  .msg{max-width:92%}
  main{padding:12px}
}
</style>
</head>
<body>
<header>
<h1>Zeline Dashboard</h1>
<span id="statusline">menghubungkan…</span>
<button id="logout" type="button">Keluar</button>
</header>
<nav id="tabs" role="tablist">
<button data-tab="ringkasan" class="active">Ringkasan</button>
<button data-tab="chat">Chat</button>
<button data-tab="workers">Workers</button>
<button data-tab="goals">Goals</button>
<button data-tab="workflows">Workflows</button>
<button data-tab="skills">Skills</button>
<button data-tab="cron">Cron</button>
<button data-tab="connectors">Connectors</button>
</nav>
<main>
<section id="pane-ringkasan" class="tabpane active">
<div class="cards" id="statcards"></div>
<p class="muted" id="ringkasan-note"></p>
</section>

<section id="pane-chat" class="tabpane" style="display:none;flex-direction:column;height:100%">
<div id="messages"></div>
<div id="typing">Zeline sedang mengetik…</div>
<div id="composer">
<textarea id="input" rows="2" placeholder="Tulis pesan… (Enter = kirim)"></textarea>
<button id="send" type="button">Kirim</button>
</div>
</section>

<section id="pane-workers" class="tabpane">
<div class="toolbar">
<button class="btn small" id="workers-refresh" type="button">Muat ulang</button>
<span class="muted" id="workers-note">auto-refresh tiap 5 detik saat tab ini aktif</span>
</div>
<div class="tablewrap"><table>
<thead><tr><th>ID</th><th>Task</th><th>Status</th><th>Identity</th></tr></thead>
<tbody id="workers-body"></tbody>
</table></div>
</section>

<section id="pane-goals" class="tabpane">
<div class="toolbar"><button class="btn small" id="goals-refresh" type="button">Muat ulang</button></div>
<div id="goals-list"></div>
</section>

<section id="pane-workflows" class="tabpane">
<div class="toolbar"><button class="btn small" id="workflows-refresh" type="button">Muat ulang</button></div>
<div class="tablewrap"><table>
<thead><tr><th>Nama</th><th>Nodes</th><th>Diubah</th><th>Aksi</th></tr></thead>
<tbody id="workflows-body"></tbody>
</table></div>
<div id="workflow-detail"></div>
</section>

<section id="pane-skills" class="tabpane">
<div class="toolbar">
<input type="search" id="skills-q" placeholder="Cari skill…">
<button class="btn small" id="skills-refresh" type="button">Muat ulang</button>
</div>
<div class="tablewrap"><table>
<thead><tr><th>Nama</th><th>Judul</th><th>Deskripsi</th><th>Scope</th></tr></thead>
<tbody id="skills-body"></tbody>
</table></div>
</section>

<section id="pane-cron" class="tabpane">
<div class="toolbar"><button class="btn small" id="cron-refresh" type="button">Muat ulang</button>
<span class="muted" id="cron-state"></span></div>
<div class="tablewrap"><table>
<thead><tr><th>ID</th><th>Jadwal</th><th>Berikutnya</th></tr></thead>
<tbody id="cron-body"></tbody>
</table></div>
</section>

<section id="pane-connectors" class="tabpane">
<div class="toolbar"><button class="btn small" id="connectors-refresh" type="button">Muat ulang</button></div>
<div class="cards" id="connectors-grid"></div>
</section>
</main>
<script>
"use strict";
var token = null;
try { token = sessionStorage.getItem("zeline_webchat_token"); } catch (ex) {}
if (!token) { location.reload(); }

function authHeaders(extra) {
  var h = {"Authorization": "Bearer " + token};
  if (extra) { for (var k in extra) { h[k] = extra[k]; } }
  return h;
}
function apiGet(path) {
  return fetch(path, {headers: authHeaders()}).then(function (resp) {
    if (resp.status === 401) { throw new Error("auth"); }
    if (!resp.ok) { throw new Error("HTTP " + resp.status); }
    return resp.json();
  });
}
// Semua teks dirender via textContent — tidak pernah innerHTML — anti XSS.
function td(text) {
  var c = document.createElement("td");
  c.textContent = (text === null || text === undefined) ? "" : String(text);
  return c;
}
function emptyRow(tbody, cols, text) {
  tbody.textContent = "";
  var tr = document.createElement("tr");
  var c = document.createElement("td");
  c.setAttribute("colspan", String(cols));
  c.className = "muted";
  c.textContent = text;
  tr.appendChild(c);
  tbody.appendChild(tr);
}
function statusBadge(st) {
  var s = document.createElement("span");
  s.className = "badge " + String(st || "").toLowerCase().replace(/[^a-z]/g, "");
  s.textContent = st || "-";
  return s;
}

/* ---------- tab switching ---------- */
var activeTab = "ringkasan";
var workersTimer = null;
var loaders = {};
document.getElementById("tabs").addEventListener("click", function (e) {
  var b = e.target.closest("button[data-tab]");
  if (!b) return;
  switchTab(b.getAttribute("data-tab"));
});
function switchTab(name) {
  activeTab = name;
  var btns = document.querySelectorAll("#tabs button");
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.toggle("active", btns[i].getAttribute("data-tab") === name);
  }
  var panes = document.querySelectorAll(".tabpane");
  for (var j = 0; j < panes.length; j++) {
    var p = panes[j];
    var on = p.id === "pane-" + name;
    p.classList.toggle("active", on);
    p.style.display = on ? (name === "chat" ? "flex" : "block") : "none";
  }
  if (workersTimer) { clearInterval(workersTimer); workersTimer = null; }
  if (name === "workers") {
    loadWorkers();
    workersTimer = setInterval(function () {
      if (activeTab === "workers") loadWorkers();
    }, 5000);
  } else if (loaders[name]) {
    loaders[name]();
  }
}

/* ---------- ringkasan ---------- */
loaders.ringkasan = function () {
  apiGet("/api/status").then(function (d) {
    var cards = document.getElementById("statcards");
    cards.textContent = "";
    var items = [
      ["Model", d.model || "-"],
      ["Worker aktif", (d.workers || []).length],
      ["Tools", d.tool_count || 0],
      ["Connectors", d.connector_count || 0],
      ["Sesi terindeks", d.session_count || 0],
      ["Skill dipelajari", d.learned_skills || 0],
      ["Backend", d.backend || "local"],
      ["Token hari ini", (d.usage_today && d.usage_today.total_tokens)
        ? Number(d.usage_today.total_tokens).toLocaleString("id-ID") : "0"]
    ];
    for (var i = 0; i < items.length; i++) {
      var card = document.createElement("div");
      card.className = "card";
      var num = document.createElement("p");
      num.className = "num";
      num.textContent = String(items[i][1]);
      var lbl = document.createElement("p");
      lbl.className = "lbl";
      lbl.textContent = items[i][0];
      card.appendChild(num); card.appendChild(lbl);
      cards.appendChild(card);
    }
    var sl = document.getElementById("statusline");
    sl.textContent = "model: " + (d.model || "-") + " · " +
      (d.workers || []).length + " worker";
  }).catch(function () {
    document.getElementById("statusline").textContent = "status tidak tersedia";
  });
};

/* ---------- chat ---------- */
var chatId = null;
try {
  chatId = sessionStorage.getItem("zeline_webchat_chat_id");
  if (!chatId) {
    chatId = "c" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem("zeline_webchat_chat_id", chatId);
  }
} catch (ex) { chatId = "default"; }
var messagesEl = document.getElementById("messages");
var inputEl = document.getElementById("input");
var sendBtn = document.getElementById("send");
var typingEl = document.getElementById("typing");
function addMessage(text, cls) {
  var div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
function sendChat() {
  var text = inputEl.value.trim();
  if (!text || sendBtn.disabled) return;
  addMessage(text, "user");
  inputEl.value = "";
  sendBtn.disabled = true;
  typingEl.style.display = "block";
  messagesEl.scrollTop = messagesEl.scrollHeight;
  fetch("/api/message", {
    method: "POST",
    headers: authHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({chat_id: chatId, text: text})
  }).then(function (resp) {
    if (resp.status === 401) { throw new Error("Tidak terautentikasi."); }
    return resp.json().then(function (data) {
      if (!resp.ok) { throw new Error(data.error || ("Server error " + resp.status)); }
      return data;
    });
  }).then(function (data) {
    addMessage(data.reply || "(balasan kosong)", "zeline");
  }).catch(function (ex) {
    addMessage("Gagal mengirim: " + (ex.message || ex), "system");
  }).then(function () {
    sendBtn.disabled = false;
    typingEl.style.display = "none";
    inputEl.focus();
  });
}
sendBtn.addEventListener("click", sendChat);
inputEl.addEventListener("keydown", function (e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
addMessage("Selamat datang di Zeline Dashboard. Token tersimpan hanya di tab ini.", "system");

/* ---------- workers ---------- */
function loadWorkers() {
  apiGet("/api/workers").then(function (d) {
    var tb = document.getElementById("workers-body");
    tb.textContent = "";
    var ws = d.workers || [];
    if (!ws.length) { emptyRow(tb, 4, "Tidak ada worker."); return; }
    for (var i = 0; i < ws.length; i++) {
      var w = ws[i];
      var tr = document.createElement("tr");
      tr.appendChild(td(w.id));
      tr.appendChild(td(w.task));
      var sc = document.createElement("td");
      sc.appendChild(statusBadge(w.status));
      tr.appendChild(sc);
      tr.appendChild(td(w.identity));
      tb.appendChild(tr);
    }
  }).catch(function () {
    emptyRow(document.getElementById("workers-body"), 4, "Gagal memuat workers.");
  });
}
loaders.workers = loadWorkers;
document.getElementById("workers-refresh").addEventListener("click", loadWorkers);

/* ---------- goals ---------- */
loaders.goals = function () {
  apiGet("/api/goals").then(function (d) {
    var list = document.getElementById("goals-list");
    list.textContent = "";
    var gs = d.goals || [];
    if (!gs.length) {
      var p = document.createElement("p");
      p.className = "muted"; p.textContent = "Belum ada goal.";
      list.appendChild(p); return;
    }
    for (var i = 0; i < gs.length; i++) {
      var g = gs[i];
      var card = document.createElement("div");
      card.className = "goalcard";
      var h = document.createElement("h3");
      h.textContent = g.title || "(tanpa judul)";
      card.appendChild(h);
      if (g.target) {
        var t = document.createElement("p");
        t.className = "muted"; t.textContent = "Target: " + g.target;
        card.appendChild(t);
      }
      var row = document.createElement("div");
      row.className = "row";
      row.appendChild(statusBadge(g.status));
      var prog = document.createElement("span");
      prog.textContent = "progress " + (g.progress || 0) + "%";
      row.appendChild(prog);
      if (g.deadline) {
        var dl = document.createElement("span");
        dl.textContent = "deadline: " + g.deadline;
        row.appendChild(dl);
      }
      card.appendChild(row);
      var bar = document.createElement("div");
      bar.className = "progress";
      var fill = document.createElement("div");
      fill.style.width = Math.min(100, Math.max(0, Number(g.progress) || 0)) + "%";
      bar.appendChild(fill);
      card.appendChild(bar);
      list.appendChild(card);
    }
  }).catch(function () {
    var list = document.getElementById("goals-list");
    list.textContent = "";
    var p = document.createElement("p");
    p.className = "muted"; p.textContent = "Gagal memuat goals.";
    list.appendChild(p);
  });
};
document.getElementById("goals-refresh").addEventListener("click", loaders.goals);

/* ---------- workflows ---------- */
loaders.workflows = function () {
  apiGet("/api/workflows").then(function (d) {
    var tb = document.getElementById("workflows-body");
    tb.textContent = "";
    var ws = d.workflows || [];
    if (!ws.length) { emptyRow(tb, 4, "Belum ada workflow."); return; }
    for (var i = 0; i < ws.length; i++) {
      (function (w) {
        var tr = document.createElement("tr");
        tr.appendChild(td(w.name));
        tr.appendChild(td(w.nodes));
        tr.appendChild(td(w.updated));
        var ac = document.createElement("td");
        var vb = document.createElement("button");
        vb.className = "btn small"; vb.type = "button"; vb.textContent = "Lihat";
        vb.addEventListener("click", function () { showWorkflow(w.id); });
        var db = document.createElement("button");
        db.className = "btn small danger"; db.type = "button";
        db.textContent = "Hapus"; db.style.marginLeft = "6px";
        db.addEventListener("click", function () { deleteWorkflow(w.id, w.name); });
        ac.appendChild(vb); ac.appendChild(db);
        tr.appendChild(ac);
        tb.appendChild(tr);
      })(ws[i]);
    }
  }).catch(function () {
    emptyRow(document.getElementById("workflows-body"), 4, "Gagal memuat workflows.");
  });
};
function showWorkflow(id) {
  apiGet("/api/workflows/" + encodeURIComponent(id)).then(function (d) {
    var el = document.getElementById("workflow-detail");
    el.textContent = "";
    var box = document.createElement("div");
    box.className = "detail";
    var title = document.createElement("strong");
    title.textContent = "Workflow: " + (d.name || id);
    box.appendChild(title);
    var pre = document.createElement("div");
    pre.textContent = JSON.stringify(
      {id: d.id, nodes: d.nodes, edges: d.edges}, null, 2);
    box.appendChild(pre);
    el.appendChild(box);
    el.scrollIntoView({behavior: "smooth", block: "nearest"});
  }).catch(function () {
    var el = document.getElementById("workflow-detail");
    el.textContent = "Gagal memuat detail workflow.";
  });
}
function deleteWorkflow(id, name) {
  if (!confirm("Hapus workflow '" + name + "'?")) return;
  fetch("/api/workflows/" + encodeURIComponent(id), {
    method: "DELETE", headers: authHeaders()
  }).then(function (resp) { return resp.json(); })
  .then(function (d) {
    if (d.ok) { loaders.workflows(); }
    else { alert("Gagal menghapus: " + (d.error || "unknown")); }
  }).catch(function (ex) { alert("Gagal menghapus: " + ex.message); });
}
document.getElementById("workflows-refresh").addEventListener("click", loaders.workflows);

/* ---------- skills ---------- */
var skillsCache = [];
loaders.skills = function () {
  apiGet("/api/skills").then(function (d) {
    skillsCache = d.skills || [];
    renderSkills();
  }).catch(function () {
    emptyRow(document.getElementById("skills-body"), 4, "Gagal memuat skills.");
  });
};
function renderSkills() {
  var q = document.getElementById("skills-q").value.trim().toLowerCase();
  var tb = document.getElementById("skills-body");
  tb.textContent = "";
  var n = 0;
  for (var i = 0; i < skillsCache.length; i++) {
    var s = skillsCache[i];
    var hay = ((s.name || "") + " " + (s.title || "") + " " + (s.description || "")).toLowerCase();
    if (q && hay.indexOf(q) < 0) continue;
    n++;
    var tr = document.createElement("tr");
    tr.appendChild(td(s.name));
    tr.appendChild(td(s.title));
    var dc = td((s.description || "").slice(0, 160));
    tr.appendChild(dc);
    tr.appendChild(td(s.scope));
    tb.appendChild(tr);
  }
  if (!n) emptyRow(tb, 4, q ? "Tidak cocok dengan pencarian." : "Belum ada skill.");
}
document.getElementById("skills-q").addEventListener("input", renderSkills);
document.getElementById("skills-refresh").addEventListener("click", loaders.skills);

/* ---------- cron ---------- */
loaders.cron = function () {
  apiGet("/api/cron").then(function (d) {
    var st = document.getElementById("cron-state");
    st.textContent = d.enabled ? "scheduler aktif" : "scheduler nonaktif";
    var tb = document.getElementById("cron-body");
    tb.textContent = "";
    var jobs = d.jobs || [];
    if (!jobs.length) { emptyRow(tb, 3, "Tidak ada job terjadwal."); return; }
    for (var i = 0; i < jobs.length; i++) {
      var tr = document.createElement("tr");
      tr.appendChild(td(jobs[i].id));
      tr.appendChild(td(jobs[i].schedule));
      tr.appendChild(td(jobs[i].next));
      tb.appendChild(tr);
    }
  }).catch(function () {
    emptyRow(document.getElementById("cron-body"), 3, "Gagal memuat cron jobs.");
  });
};
document.getElementById("cron-refresh").addEventListener("click", loaders.cron);

/* ---------- connectors ---------- */
loaders.connectors = function () {
  apiGet("/api/connectors").then(function (d) {
    var grid = document.getElementById("connectors-grid");
    grid.textContent = "";
    var cs = d.connectors || [];
    if (!cs.length) {
      var p = document.createElement("p");
      p.className = "muted"; p.textContent = "Belum ada connector.";
      grid.appendChild(p); return;
    }
    for (var i = 0; i < cs.length; i++) {
      var card = document.createElement("div");
      card.className = "card";
      var nm = document.createElement("p");
      nm.className = "num"; nm.style.fontSize = "16px";
      nm.textContent = cs[i].name;
      var st = document.createElement("p");
      st.className = "lbl";
      st.appendChild(statusBadge(cs[i].status));
      card.appendChild(nm); card.appendChild(st);
      grid.appendChild(card);
    }
  }).catch(function () {
    var grid = document.getElementById("connectors-grid");
    grid.textContent = "Gagal memuat connectors.";
  });
};
document.getElementById("connectors-refresh").addEventListener("click", loaders.connectors);

/* ---------- logout ---------- */
document.getElementById("logout").addEventListener("click", function () {
  try {
    sessionStorage.removeItem("zeline_webchat_token");
    sessionStorage.removeItem("zeline_webchat_chat_id");
  } catch (ex) {}
  location.reload();
});

/* init */
loaders.ringkasan();
</script>
</body>
</html>

"""

# Alias lama: halaman utama sekarang dashboard penuh (chat jadi salah satu tab).
_CHAT_HTML = _DASHBOARD_HTML


def info() -> dict[str, str]:
    return {
        "label": "WebChat UI",
        "hint": "GET / chat dashboard (owner token) on localhost.",
    }


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    token = str(cfg.get("token", ""))
    if len(token) < 16:
        errors.append("webchat token empty/too short (minimum 16 characters)")
    try:
        token.encode("ascii")
    except UnicodeEncodeError:
        # Header HTTP di-decode sebagai latin-1 sementara token config bisa
        # berupa str Unicode apa pun: token non-ASCII membuat perbandingan
        # hmac.compare_digest berperilaku inkonsisten antar encoding.
        errors.append(
            "webchat token must be ASCII-only (non-ASCII tokens compare inconsistently via hmac)"
        )
    try:
        port = int(cfg.get("port", DEFAULT_PORT))
        if not 1 <= port <= 65535:
            errors.append("webchat port must be 1–65535")
    except (TypeError, ValueError):
        errors.append("invalid webchat port")
    host = str(cfg.get("host", "127.0.0.1"))
    if not host.strip():
        # Host kosong di HTTPServer stdlib mengikat ke SEMUA interface
        # (setara 0.0.0.0) — bukan loopback. Tolak eksplisit (fail-closed),
        # bukan dinormalkan diam-diam: operator harus menulis host secara
        # eksplisit (default aman: 127.0.0.1). Pilihan ini konsisten dengan
        # perlakuan host unspecified di cli (tidak pernah bind diam-diam),
        # tapi karena gateway jalan non-interaktif tidak ada konfirmasi —
        # jadi kosong = ditolak, bukan diperingatkan-lalu-jalan.
        errors.append(
            "webchat host is empty: an empty host would silently bind all "
            "interfaces (0.0.0.0). Set it explicitly (default 127.0.0.1)."
        )
    if str(cfg.get("tool_profile", "safe")) != "safe":
        errors.append(
            "webchat tool_profile must be 'safe' (single shared bearer token "
            "cannot prove owner identity; elevated tools need an owner-allowlisted gateway)"
        )
    return errors


def _is_authorized(headers, token: str) -> bool:
    supplied = headers.get("X-Zeline-Token", "")
    authorization = headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, token)


def _credential_present(headers) -> bool:
    """True bila request membawa kredensial (benar atau salah)."""
    return bool(headers.get("X-Zeline-Token", "").strip()) or bool(
        headers.get("Authorization", "").strip()
    )


def _running_workers() -> list[dict[str, Any]]:
    """Best-effort compact list of live workers (running/queued). Never raises."""
    try:
        from zeline import supervisor as _supervisor

        registry = getattr(_supervisor, "_SUPERVISORS", None)
        guard = getattr(_supervisor, "_SUPERVISORS_GUARD", None)
        if not isinstance(registry, dict) or guard is None:
            return []
        with guard:
            supervisors = list(registry.values())
        workers: list[dict[str, Any]] = []
        for supervisor in supervisors:
            for item in supervisor.list_workers():
                if str(item.get("status")) in ("running", "queued"):
                    workers.append(
                        {
                            "id": str(item.get("id", "")),
                            "task": str(item.get("task", ""))[:80],
                            "status": str(item.get("status", "")),
                        }
                    )
        return workers[:25]
    except Exception:
        return []


def _status_payload() -> dict[str, Any]:
    """Status ringkas; semua sumber best-effort, tidak pernah bocor rahasia."""
    model: str | None = None
    try:
        from zeline import config as _config

        model = str(_config.stored_config_copy().get("provider", {}).get("model", "") or "") or None
    except Exception:
        model = None
    usage: dict[str, Any] = {}
    try:
        from zeline import usage_stats

        if usage_stats.enabled():
            store = usage_stats.UsageStore()
            totals = store.totals(usage_stats.since_day_for(1))
            usage = {
                "prompt_tokens": int(totals.get("prompt_tokens", 0)),
                "completion_tokens": int(totals.get("completion_tokens", 0)),
                "total_tokens": int(totals.get("total_tokens", 0)),
                "calls": int(totals.get("calls", 0)),
            }
    except Exception:
        usage = {}
    # Extended stats for desktop dashboard
    tool_count = 0
    connector_count = 0
    session_count = 0
    learned_count = 0
    backend = "local"
    try:
        from zeline import tools as _tools
        tool_count = len(_tools.TOOL_DEFS)
    except Exception:
        pass
    try:
        from zeline import connectors as _conns
        connector_count = len(_conns.all_ids())
    except Exception:
        pass
    try:
        from zeline import session_search
        session_count = session_search.index_stats().get("documents", 0)
    except Exception:
        pass
    try:
        from zeline import learning
        learned_count = len(learning.list_learned_skills())
    except Exception:
        pass
    try:
        from zeline import config as _cfg
        backend = str(getattr(_cfg, "EXECUTION_BACKEND", "local"))
    except Exception:
        pass
    return {
        "ok": True,
        "model": model,
        "workers": _running_workers(),
        "usage_today": usage,
        "tool_count": tool_count,
        "connector_count": connector_count,
        "session_count": session_count,
        "learned_skills": learned_count,
        "backend": backend,
    }


def _connectors_payload() -> dict[str, Any]:
    """List configured connectors (no secrets)."""
    items = []
    try:
        from zeline import connectors as _conns
        for cid in _conns.all_ids():
            items.append({
                "name": cid,
                "status": "available",
            })
    except Exception:
        pass
    return {"ok": True, "connectors": items}


def _skills_payload() -> dict[str, Any]:
    """List available skills (no content, just metadata)."""
    items = []
    try:
        from zeline import skills as _skills
        for scope, name, title, desc, _ in _skills.list_skill_entries():
            items.append({"name": name, "title": title, "description": desc, "scope": scope})
    except Exception:
        pass
    return {"ok": True, "skills": items}


def _workflows_payload() -> dict[str, Any]:
    """List saved workflows."""
    try:
        from zeline import workflows as _wf
        return {"ok": True, "workflows": _wf.list_workflows()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "workflows": []}


def _workflow_get_payload(wf_id: str) -> dict[str, Any]:
    try:
        from zeline import workflows as _wf
        data = _wf.get_workflow(wf_id)
        if data is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True, **data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _workflow_executions_payload(wf_id: str) -> dict[str, Any]:
    """List executions of one workflow (newest first)."""
    try:
        from zeline import workflows as _wf
        return {"ok": True, "executions": _wf.list_executions(wf_id)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "executions": []}


def _execution_payload(exec_id: str) -> dict[str, Any]:
    """Full execution record (node statuses, log)."""
    try:
        from zeline import workflows as _wf
        data = _wf.get_execution(exec_id)
        if data is None:
            return {"ok": False, "error": "not found"}
        return {"ok": True, **data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


class _WorkflowAgentAdapter:
    """Duck-typed agent for workflows.execute_workflow, backed by the
    webchat session store (safe profile, per-execution identity)."""

    def __init__(self, sessions, identity: str, tool_profile: str):
        self._sessions = sessions
        self._identity = identity
        self._tool_profile = tool_profile

    def send(self, text: str) -> str:
        return self._sessions.send(
            identity=self._identity, text=text, tool_profile=self._tool_profile
        )


# ---------------------------------------------------------------------------
# Fail-fast renderer untuk interaction.ask() di identitas webchat
# ---------------------------------------------------------------------------
#
# WebChat tidak punya jalur pertanyaan interaktif: tidak ada picker seperti
# Telegram, tidak ada stdin seperti CLI. Tanpa renderer terdaftar,
# ``interaction.ask()`` jatuh ke ``event.wait(180)`` — POST /api/message hang
# 3 menit SEMENTARA session lock tertahan, sehingga chat_id itu terblokir
# total. Renderer ini mengembalikan string deny SEGERA, yang membuat
# ``interaction.ask()`` short-circuit: tanpa wait, tanpa lock tertahan.
#
# Identitas webchat dinamis (``webchat:<chat_id>``) sehingga renderer TIDAK
# bisa didaftarkan sekali di ``start()``; ia didaftarkan per turn di handler
# /api/message lewat ``_register_ask_renderer`` / ``_unregister_ask_renderer``
# di bawah. Refcount menjaga dua turn berbarengan untuk chat_id yang sama
# (yang kedua antre di session lock): unregister dari turn pertama tidak
# boleh mencabut renderer selagi turn kedua masih membutuhkannya.
_WEBCHAT_ASK_DENY = (
    "Deny — WebChat tidak dapat menampilkan permintaan persetujuan secara "
    "interaktif (tidak ada picker), sehingga setiap aksi yang memerlukan "
    "persetujuan otomatis DITOLAK di sini (fail-closed). Untuk menyetujui "
    "aksi semacam ini, gunakan Telegram atau CLI."
)


def _workers_payload() -> dict[str, Any]:
    """List workers (all statuses, most recent first)."""
    try:
        from zeline import supervisor as _supervisor

        registry = getattr(_supervisor, "_SUPERVISORS", None)
        guard = getattr(_supervisor, "_SUPERVISORS_GUARD", None)
        workers: list[dict[str, Any]] = []
        if isinstance(registry, dict) and guard is not None:
            with guard:
                supervisors = list(registry.values())
            for sup in supervisors:
                for item in sup.list_workers():
                    workers.append(
                        {
                            "id": str(item.get("id", "")),
                            "identity": str(getattr(sup, "identity", "")),
                            "task": str(item.get("task", ""))[:120],
                            "status": str(item.get("status", "")),
                        }
                    )
        return {"ok": True, "workers": workers[:50]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "workers": []}


def _goals_payload() -> dict[str, Any]:
    """List goals across all identities (scan goals dir)."""
    try:
        from zeline import goals as _g
        all_goals = []
        try:
            gdir = _g.goals_dir()
            if gdir.is_dir():
                for f in gdir.glob("*.json"):
                    try:
                        # Filename is hash of identity; read file directly
                        import json
                        data = json.loads(f.read_text())
                        if isinstance(data, list):
                            all_goals.extend(data)
                        elif isinstance(data, dict) and "goals" in data:
                            all_goals.extend(data["goals"])
                    except Exception:
                        pass
        except Exception:
            pass
        return {"ok": True, "goals": all_goals}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "goals": []}


def _sessions_payload() -> dict[str, Any]:
    """Session info. Currently exposes active session via /api/status."""
    try:
        status = _status_payload()
        return {
            "ok": True,
            "sessions": [],
            "active_session": {
                "model": status.get("model"),
                "workers": status.get("workers", 0),
            },
            "note": "Full session history via FTS5 search_sessions tool",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "sessions": []}


def _cron_payload() -> dict[str, Any]:
    """List scheduled jobs."""
    try:
        from zeline import scheduler as _cron
        jobs = []
        enabled = _cron.enabled()
        if enabled:
            for job in _cron.list_jobs():
                jobs.append({
                    "id": job.id,
                    "schedule": job.parsed().describe(),
                    "next": _cron.describe_next_run(job),
                })
        return {"ok": True, "jobs": jobs, "enabled": enabled}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "jobs": []}



def _webchat_deny_ask(entry: object) -> str:
    """Renderer fail-fast: tolak seketika, tanpa menyentuh network/event.

    Tidak pernah raise (mengembalikan string konstan saja), tidak membaca
    isi pertanyaan (tidak membocorkan detail perintah ke mana pun), dan
    tidak pernah me-allow: ``approvals.parse_verdict`` memetakan string apa
    pun di luar allow/allow-session menjadi "deny".
    """
    return _WEBCHAT_ASK_DENY


_ASK_GUARD_LOCK = threading.Lock()
_ASK_GUARD_COUNT: dict[str, int] = {}


def _register_ask_renderer(identity: str) -> None:
    """Daftarkan renderer deny untuk satu turn; aman dipanggil berulang."""
    with _ASK_GUARD_LOCK:
        _ASK_GUARD_COUNT[identity] = _ASK_GUARD_COUNT.get(identity, 0) + 1
        interaction.register_channel(identity, _webchat_deny_ask)


def _unregister_ask_renderer(identity: str) -> None:
    """Lepas renderer; hanya benar-benar unregister saat turn terakhir pergi."""
    with _ASK_GUARD_LOCK:
        remaining = _ASK_GUARD_COUNT.get(identity, 0) - 1
        if remaining > 0:
            _ASK_GUARD_COUNT[identity] = remaining
        else:
            _ASK_GUARD_COUNT.pop(identity, None)
            interaction.unregister_channel(identity)


def start(
    sessions,
    cfg: dict[str, Any],
    stop_event,
    ready: Callable[[int], None] | None = None,
) -> None:
    host = str(cfg.get("host", "127.0.0.1"))
    if not host.strip():
        # Backstop bila start() dipanggil tanpa lewat validate_config: host
        # kosong = bind semua interface diam-diam; tolak keras (fail-closed).
        raise ValueError("webchat host is empty — refusing to bind all interfaces implicitly")
    port = int(cfg.get("port", DEFAULT_PORT))
    token = str(cfg["token"])
    tool_profile = str(cfg.get("tool_profile", "safe"))

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZelineWebChat/0.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _html(self, status: int, html: str) -> None:
            self._send(status, html.encode("utf-8"), "text/html; charset=utf-8")

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/health":
                self._json(200, {"ok": True, "service": "zeline-webchat"})
                return
            if path == "/":
                if _is_authorized(self.headers, token):
                    self._html(200, _CHAT_HTML)
                elif _credential_present(self.headers):
                    # Token salah: 401 agar brute-force terdeteksi.
                    self._json(401, {"error": "unauthorized"})
                else:
                    # Tanpa token: halaman login SAJA (tanpa data/panel chat).
                    self._html(200, _LOGIN_HTML)
                return
            if path == "/api/status":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _status_payload())
                return
            if path == "/api/connectors":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _connectors_payload())
                return
            if path == "/api/skills":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _skills_payload())
                return
            if path == "/api/workflows":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _workflows_payload())
                return
            if path.startswith("/api/workflows/"):
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                rest = path[len("/api/workflows/"):].strip("/")
                if rest.endswith("/executions"):
                    wf_id = rest[: -len("/executions")].rstrip("/")
                    self._json(200, _workflow_executions_payload(wf_id))
                    return
                self._json(200, _workflow_get_payload(rest))
                return
            if path.startswith("/api/executions/"):
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                exec_id = path[len("/api/executions/"):].strip("/").split("/")[0]
                self._json(200, _execution_payload(exec_id))
                return
            if path == "/api/workers":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _workers_payload())
                return
            if path == "/api/goals":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _goals_payload())
                return
            if path == "/api/sessions":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _sessions_payload())
                return
            if path == "/api/cron":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, _cron_payload())
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            # Rate limit dulu, sebelum auth: banjir dari satu IP ditolak
            # murah (429) tanpa membakar satu turn agen pun.
            ip = self.client_address[0]
            if not _POST_LIMITER.allow(ip):
                self._json(429, {"error": "rate limited, try again later"})
                return
            _post_path = self.path.split("?", 1)[0].rstrip("/")
            if _post_path == "/api/workflows":
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(400, {"error": "invalid content length"})
                    return
                # M4 fix: enforce body size cap (was unbounded)
                if not 0 < length <= MAX_BODY_BYTES:
                    self._json(413, {"error": f"body must be 1–{MAX_BODY_BYTES} bytes"})
                    return
                try:
                    body = json.loads(self.rfile.read(length))
                except Exception:
                    self._json(400, {"error": "invalid JSON"})
                    return
                try:
                    from zeline import workflows as _wf
                    wid = _wf.save_workflow(
                        body.get("id"), str(body.get("name", "Untitled")),
                        body.get("nodes", []), body.get("edges", []),
                    )
                    self._json(200, {"ok": True, "id": wid})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                return
            if _post_path.startswith("/api/workflows/") and _post_path.endswith("/execute"):
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    self._json(400, {"error": "invalid content length"})
                    return
                if length > MAX_BODY_BYTES:
                    self._json(413, {"error": f"body must be 1–{MAX_BODY_BYTES} bytes"})
                    return
                try:
                    body = json.loads(self.rfile.read(length)) if length else {}
                except Exception:
                    self._json(400, {"error": "invalid JSON"})
                    return
                if not isinstance(body, dict):
                    self._json(400, {"error": "JSON body must be an object"})
                    return
                wf_id = _post_path[len("/api/workflows/"): -len("/execute")].strip("/")
                try:
                    node_timeout = float(body.get("node_timeout", 300))
                    approval_timeout = float(body.get("approval_timeout", 1800))
                except (TypeError, ValueError):
                    self._json(400, {"error": "timeouts must be numbers"})
                    return
                chat_id = str(body.get("chat_id", "default")).strip()[:256] or "default"
                try:
                    from zeline import workflows as _wf
                    adapter = _WorkflowAgentAdapter(
                        sessions, f"webchat:wf:{chat_id}", tool_profile
                    )
                    exec_id = _wf.execute_workflow(
                        wf_id, adapter,
                        node_timeout=node_timeout,
                        approval_timeout=approval_timeout,
                    )
                    self._json(200, {"ok": True, "exec_id": exec_id})
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                except Exception:
                    print("  [webchat] unhandled workflow execute error", flush=True)
                    self._json(500, {"error": "internal error"})
                return
            if _post_path.startswith("/api/executions/") and _post_path.endswith("/pause"):
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                exec_id = _post_path[len("/api/executions/"): -len("/pause")].strip("/").split("/")[0]
                try:
                    from zeline import workflows as _wf
                    ok = _wf.pause_workflow(exec_id)
                    self._json(200, {"ok": ok})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                return
            if _post_path.startswith("/api/executions/") and _post_path.endswith("/resume"):
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    self._json(400, {"error": "invalid content length"})
                    return
                if length > MAX_BODY_BYTES:
                    self._json(413, {"error": f"body must be 1–{MAX_BODY_BYTES} bytes"})
                    return
                try:
                    body = json.loads(self.rfile.read(length)) if length else {}
                except Exception:
                    self._json(400, {"error": "invalid JSON"})
                    return
                if not isinstance(body, dict):
                    self._json(400, {"error": "JSON body must be an object"})
                    return
                exec_id = _post_path[len("/api/executions/"): -len("/resume")].strip("/").split("/")[0]
                approved = body.get("approved", True)
                try:
                    from zeline import workflows as _wf
                    ok = _wf.resume_workflow(exec_id, approved=bool(approved))
                    self._json(200, {"ok": ok})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                return
            if _post_path != "/api/message":
                self._json(404, {"error": "not found"})
                return
            if not _is_authorized(self.headers, token):
                self._json(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid content length"})
                return
            if not 0 < length <= MAX_BODY_BYTES:
                self._json(413, {"error": f"body must be 1–{MAX_BODY_BYTES} bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "invalid JSON"})
                return
            if not isinstance(body, dict):
                self._json(400, {"error": "JSON body must be an object"})
                return
            chat_id = str(body.get("chat_id", "default")).strip()
            text = str(body.get("text", "")).strip()
            if not chat_id or len(chat_id) > 256:
                self._json(400, {"error": "invalid chat_id"})
                return
            if not text:
                self._json(400, {"error": "text is required"})
                return
            identity = f"webchat:{chat_id}"
            # Daftarkan renderer fail-fast SELAMA turn: tanpa ini, tool yang
            # butuh approval membuat interaction.ask() hang 180 dtk sambil
            # session lock tertahan (chat_id terblokir total).
            _register_ask_renderer(identity)
            try:
                reply = sessions.send(
                    identity=identity,
                    text=text,
                    tool_profile=tool_profile,
                )
                self._json(200, {"reply": reply})
            except ZelineError as exc:
                self._json(502, {"error": str(exc)})
            except Exception:
                print("  [webchat] unhandled agent error", flush=True)
                self._json(500, {"error": "internal agent error"})
            finally:
                _unregister_ask_renderer(identity)


        def do_DELETE(self) -> None:
            """DELETE /api/workflows/<id>"""
            _del_path = self.path.split("?", 1)[0].rstrip("/")
            if _del_path.startswith("/api/workflows/"):
                if not _is_authorized(self.headers, token):
                    self._json(401, {"error": "unauthorized"})
                    return
                wid = _del_path[len("/api/workflows/"):].strip("/")
                try:
                    from zeline import workflows as _wf
                    ok = _wf.delete_workflow(wid)
                    self._json(200, {"ok": ok})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                return
            self._json(404, {"error": "not found"})

        def log_message(self, _format: str, *_args: Any) -> None:
            # Jangan log payload atau Authorization token.
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.timeout = 0.5
    bound_port = int(server.server_address[1])
    if ready is not None:
        ready(bound_port)
    print(f"  [webchat] listening http://{host}:{bound_port} (/health, /, /api/*)", flush=True)
    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        print("  [webchat] stopped", flush=True)
