#!/usr/bin/env python3
"""Verifikasi END-TO-END gateway Zeline App: agent ASLI, bukan mock.

Yang dibuktikan:
  1. /auth/login menolak token salah, menerima token benar
  2. endpoint terproteksi menolak tanpa Bearer
  3. POST /sessions/{id}/messages?stream=true → delta nyata dari provider
  4. tool.started/tool.output/tool.completed muncul saat agent benar-benar
     menjalankan tool (prompt memaksa run_shell)
  5. model MENGOLAH nonce acak (membalik string yang belum pernah ada) —
     mock scripted bisa mengulang teks, tapi tidak bisa membalik input baru
  6. cancel menghentikan generation yang sedang jalan, dan cancel kedua
     idempoten
  7. history + tool_events tersimpan dan bisa dibaca ulang, tanpa secret

Pakai: python3 verify_zeline_app_real.py [BASE] [TOKEN] [MODEL]
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082") + "/api/v1"
GATEWAY_TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devlocalzelineapptoken2026"
# Model verifikasi bisa dioverride: default config bisa menunjuk route yang
# sedang down, dan itu bukan bug gateway.
MODEL = os.environ.get("ZELINE_VERIFY_MODEL", "").strip() or (
    sys.argv[3] if len(sys.argv) > 3 else "")

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(label + (f" — {detail}" if detail else ""))
    print(("PASS " if condition else "FAIL ") + label + (f" — {detail}" if detail else ""), flush=True)


def call(path: str, method: str = "GET", body: dict | None = None,
         token: str | None = None, timeout: int = 60):
    request = urllib.request.Request(BASE + path, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(request, data=data, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def stream(session_id: str, content: str, token: str, timeout: int = 300,
           collect: list | None = None, ready: threading.Event | None = None):
    """Konsumsi SSE, kembalikan daftar event."""
    request = urllib.request.Request(
        f"{BASE}/sessions/{session_id}/messages?stream=true", method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + token)
    events = collect if collect is not None else []
    with urllib.request.urlopen(request, data=json.dumps({"content": content}).encode(),
                                timeout=timeout) as response:
        buffer = ""
        for raw in response:
            buffer += raw.decode("utf-8", "replace")
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                for line in frame.split("\n"):
                    if line.startswith("data: "):
                        try:
                            events.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
                        if ready and not ready.is_set():
                            ready.set()
    return events


def main() -> int:
    # 1. auth
    status, _ = call("/auth/login", "POST", {"gateway_token": "token-salah-sekali"})
    check("login menolak token salah", status == 401, f"HTTP {status}")

    status, payload = call("/auth/login", "POST", {"gateway_token": GATEWAY_TOKEN})
    token = payload.get("data", {}).get("access_token", "")
    check("login menerima token benar", status == 200 and bool(token), f"HTTP {status}")
    if not token:
        return 1

    status, _ = call("/agents")
    check("endpoint terproteksi menolak tanpa Bearer", status == 401, f"HTTP {status}")

    status, payload = call("/agents", token=token)
    agents = payload.get("data", {}).get("agents", [])
    check("GET /agents dengan token", status == 200 and bool(agents), f"{len(agents)} agent")

    # Agent khusus verifikasi: pakai provider aktif + model default config,
    # bukan agent fixture lama yang mungkin menunjuk model bohongan.
    #
    # Gateway membatasi satu akun = satu agent (MAX_AGENTS), jadi POST /agents
    # WAJAR menolak dengan 409 di instance yang sudah punya agent. Itu bukan
    # kegagalan gateway; yang diverifikasi adalah kontraknya: 201 dengan model
    # nyata, ATAU 409 LIMIT_REACHED yang jelas. Kalau ditolak, verifikasi
    # lanjut memakai agent yang ada supaya sisa tes (tool nyata, streaming,
    # cancel, history) tetap jalan — dulu script langsung KeyError di sini.
    status, payload = call("/agents", "POST", {
        "name": "Verify Real",
        "description": "agent untuk verifikasi end-to-end",
        "system_instructions": "Jawab ringkas. Gunakan tool bila diminta.",
        **({"model": MODEL} if MODEL else {}),
    }, token=token)
    verify_agent = payload.get("data", {})
    created = status == 201 and bool(verify_agent.get("id"))
    limit_hit = status == 409 and payload.get("error", {}).get("code") == "LIMIT_REACHED"
    check("POST /agents: 201 dengan model nyata, atau 409 LIMIT_REACHED",
          (created and bool(verify_agent.get("model"))) or limit_hit,
          f"HTTP {status} " + (f"{verify_agent.get('id')} model={verify_agent.get('model')}"
                               if created else str(payload.get("error", {}).get("code"))))

    if not created:
        # Pilih agent yang modelnya masuk akal: fixture lama sering menyimpan
        # model placeholder ("test") yang pasti gagal di provider.
        usable = next((a for a in agents
                       if "/" in str(a.get("model", "")) and a.get("provider")), None)
        verify_agent = usable or (agents[0] if agents else {})
        check("agent existing dipakai untuk sisa verifikasi",
              bool(verify_agent.get("id")),
              f"{verify_agent.get('id')} model={verify_agent.get('model')}")

    status, payload = call("/providers", token=token)
    providers = payload.get("data", {}).get("providers", [])
    leaked = [p for p in providers if "api_key" in p]
    # Report the SHAPE of the hint, never the hint itself. Printing a masked
    # value into a log is still printing a credential-derived string, and this
    # script's whole point is that credentials do not travel.
    hint = str(providers[0].get("api_key_hint", "")) if providers else ""
    check("provider key tidak pernah bocor", not leaked and bool(providers),
          f"{len(providers)} provider, hint masked={'yes' if '••' in hint else 'no'}")

    agent_id = str(verify_agent.get("id") or "")
    if not agent_id:
        check("ada agent untuk dipakai verifikasi", False, "roster kosong")
        return 1

    # 2. tool nyata
    status, payload = call("/sessions", "POST",
                           {"agent_id": agent_id, "title": "verify tool"}, token=token)
    session_tool = payload.get("data", {}).get("id", "")
    check("POST /sessions", status == 201 and bool(session_tool), session_tool)

    prompt_tool = ("Jalankan perintah shell ini apa adanya dan laporkan output "
                   "mentahnya: echo ZELINE_REAL_$((6*7))")
    started = time.time()
    events = stream(session_tool, prompt_tool, token)
    types = [e.get("type") for e in events]
    deltas = [e for e in events if e.get("type") == "assistant.delta"]
    tool_started = [e for e in events if e.get("type") == "tool.started"]
    tool_output = "".join(e.get("content", "") for e in events
                          if e.get("type") == "tool.output")
    completed = next((e for e in events if e.get("type") == "assistant.completed"), None)
    errored = next((e for e in events if e.get("type") == "stream.error"), None)

    check("stream.started terkirim", "stream.started" in types)
    check("ada assistant.delta dari provider", len(deltas) > 1, f"{len(deltas)} delta")
    check("tidak ada stream.error", errored is None,
          str(errored.get("message"))[:120] if errored else "")
    check("tool benar-benar dijalankan agent", bool(tool_started),
          ", ".join(sorted({e.get("tool", "") for e in tool_started})))
    check("output tool ASLI (ZELINE_REAL_42 dari shell)",
          "ZELINE_REAL_42" in tool_output,
          tool_output.strip().replace("\n", " ")[:120])
    check("assistant.completed terkirim", completed is not None,
          f"{time.time() - started:.1f}s, {len(''.join(d.get('content','') for d in deltas))} char")

    # 3. Bukan mock scripted: model harus MENGOLAH input yang tidak bisa
    #    diketahui sebelumnya. Versi lama meminta "angka acak" lalu menuntut dua
    #    jawaban berbeda — itu menguji sampling provider, bukan gateway, dan
    #    gagal secara sah ketika model memilih angka favorit yang sama dua kali
    #    (teramati: 4728 vs 4728). Sekarang setiap sesi mendapat nonce acak dan
    #    harus mengembalikannya dalam bentuk terbalik: mock bisa mengulang teks,
    #    tapi tidak bisa membalik string yang belum pernah dilihatnya.
    answers: list[tuple[str, str]] = []
    for _ in range(2):
        nonce = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        _s, p = call("/sessions", "POST", {"agent_id": agent_id, "title": "verify vary"},
                     token=token)
        sid = p.get("data", {}).get("id", "")
        events = stream(sid, f"Balik urutan karakter string ini dan jawab HANYA hasilnya, "
                             f"tanpa penjelasan: {nonce}", token)
        text = "".join(e.get("content", "") for e in events
                       if e.get("type") == "assistant.delta").strip()
        answers.append((nonce, text))
    reversed_ok = [nonce[::-1] in text.upper() for nonce, text in answers]
    check("model mengolah input unik (bukan mock scripted)",
          all(reversed_ok),
          " | ".join(f"{nonce}→{text[:20]}" for nonce, text in answers))
    check("dua sesi menghasilkan jawaban berbeda",
          answers[0][1] != answers[1][1] and all(text for _, text in answers),
          f"[{answers[0][1][:30]}] vs [{answers[1][1][:30]}]")

    # 4. cancel di tengah generation
    _s, p = call("/sessions", "POST", {"agent_id": agent_id, "title": "verify cancel"},
                 token=token)
    session_cancel = p.get("data", {}).get("id", "")
    collected: list = []
    first_event = threading.Event()

    def run_long():
        try:
            stream(session_cancel,
                   "Tuliskan penjelasan panjang dan bertele-tele tentang cara kerja "
                   "HTTP, minimal 800 kata, jangan berhenti.",
                   token, collect=collected, ready=first_event)
        except Exception:
            pass

    worker = threading.Thread(target=run_long, daemon=True)
    worker.start()
    first_event.wait(timeout=90)
    time.sleep(4)
    status, payload = call(f"/sessions/{session_cancel}/cancel", "POST", {}, token=token)
    cancel_data = payload.get("data", {})
    check("cancel mengembalikan cancelled=True untuk stream aktif",
          status == 200 and cancel_data.get("cancelled") is True, json.dumps(cancel_data))
    worker.join(timeout=90)
    cancel_types = [e.get("type") for e in collected]
    check("stream.cancelled diterima client", "stream.cancelled" in cancel_types,
          " → ".join(dict.fromkeys(cancel_types)))
    status, payload = call(f"/sessions/{session_cancel}/cancel", "POST", {}, token=token)
    check("cancel idempoten (stream mati → cancelled=False, tetap 200)",
          status == 200 and payload.get("data", {}).get("cancelled") is False)

    # 5. history persist
    status, payload = call(f"/sessions/{session_tool}/messages", token=token)
    messages = payload.get("data", {}).get("messages", [])
    roles = [m.get("role") for m in messages]
    tool_meta = [m for m in messages
                 if m.get("metadata", {}).get("tool_events")]
    check("history tersimpan (user + assistant)",
          status == 200 and roles.count("user") >= 1 and roles.count("assistant") >= 1,
          " ".join(roles))
    check("tool_events tercatat di metadata pesan", bool(tool_meta),
          json.dumps(tool_meta[0]["metadata"]["tool_events"])[:120] if tool_meta else "")
    check("history bebas secret",
          not any(k in json.dumps(messages).lower()
                  for k in ('"api_key"', '"secret"', '"password"')))

    print(f"\n=== {len(PASS)} PASS / {len(FAIL)} FAIL ===", flush=True)
    for item in FAIL:
        print("  ✗ " + item, flush=True)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
