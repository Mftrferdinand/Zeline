#!/usr/bin/env python3
"""airdrop_registry.py — kelola registry airdrop untuk skill airdrop-manager.

Registry hidup di ~/airdrop-registry/<slug>.json (+ _index.md tabel manusia).
Tidak menyimpan seed phrase / private key — hanya metadata task & (opsional)
bot token untuk laporan Telegram satu arah.

Perintah:
  add <slug> --name N --link URL [--chain C] [--mechanic a,b] [--task "t1;t2"]
             [--tge T] [--reward R] [--wallet-sign] [--deadline D] [--mode A|B|C]
  set-bot <slug> --token TOKEN --chat-id ID     # simpan bot mode B (chmod 600)
  set-cron <slug> --job-id ID                   # catat id cron dari schedule_task
  checkin <slug>                                # increment streak, set last_checkin
  report <slug> [--send]                        # cetak laporan; --send POST ke bot
  list                                          # tabel semua airdrop
  show <slug>                                   # detail JSON satu airdrop

Semua output plain text agar mudah dibaca cron & agent.
"""
import argparse
import json
import os
import sys
import datetime
import urllib.request
import urllib.parse

HOME = os.path.expanduser("~")
REG_DIR = os.path.join(HOME, "airdrop-registry")


def _ensure_dir():
    os.makedirs(REG_DIR, exist_ok=True)


def _path(slug):
    return os.path.join(REG_DIR, f"{slug}.json")


def _load(slug):
    p = _path(slug)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save(entry):
    _ensure_dir()
    p = _path(entry["slug"])
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
    # registry bisa berisi bot token → batasi permission
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    _rebuild_index()


def _all_entries():
    _ensure_dir()
    out = []
    for fn in sorted(os.listdir(REG_DIR)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(REG_DIR, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def _rebuild_index():
    rows = ["# Airdrop Registry", "",
            "| Project | Chain | Mechanic | TGE | Mode | Streak |",
            "|---|---|---|---|---|---|"]
    for e in _all_entries():
        rows.append(
            f"| {e.get('name','?')} | {e.get('chain','—')} | "
            f"{'+'.join(e.get('mechanic',[])) or '—'} | {e.get('tge','—')} | "
            f"{e.get('delivery_mode','—')} | {e.get('streak',0)} |"
        )
    with open(os.path.join(REG_DIR, "_index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


def cmd_add(a):
    if _load(a.slug):
        print(f"EXISTS: {a.slug} sudah ada di registry — pakai 'checkin' atau edit manual.")
        return 1
    entry = {
        "name": a.name or a.slug,
        "slug": a.slug,
        "link": a.link or "",
        "chain": a.chain or "—",
        "mechanic": [m.strip() for m in (a.mechanic or "").split(",") if m.strip()],
        "daily_task": [t.strip() for t in (a.task or "").split(";") if t.strip()],
        "wallet_sign": bool(a.wallet_sign),
        "tge": a.tge or "belum diumumkan",
        "reward_est": a.reward or "spekulatif",
        "deadline": a.deadline or "unknown",
        "delivery_mode": a.mode or "A",
        "bot_token": None,
        "bot_chat_id": None,
        "cron_job_id": None,
        "streak": 0,
        "last_checkin": None,
        "created": datetime.date.today().isoformat(),
    }
    _save(entry)
    print(f"OK: airdrop '{a.slug}' ditambahkan (mode {entry['delivery_mode']}).")
    return 0


def cmd_set_bot(a):
    e = _load(a.slug)
    if not e:
        print(f"NOT FOUND: {a.slug}")
        return 1
    e["bot_token"] = a.token
    e["bot_chat_id"] = a.chat_id
    e["delivery_mode"] = "B"
    _save(e)
    print(f"OK: bot laporan untuk '{a.slug}' tersimpan (mode B).")
    return 0


def cmd_set_cron(a):
    e = _load(a.slug)
    if not e:
        print(f"NOT FOUND: {a.slug}")
        return 1
    e["cron_job_id"] = a.job_id
    _save(e)
    print(f"OK: cron job {a.job_id} dicatat untuk '{a.slug}'.")
    return 0


def cmd_checkin(a):
    e = _load(a.slug)
    if not e:
        print(f"NOT FOUND: {a.slug}")
        return 1
    today = datetime.date.today().isoformat()
    if e.get("last_checkin") == today:
        print(f"ALREADY: '{a.slug}' sudah check-in hari ini. Streak {e['streak']} hari.")
        return 0
    # streak: lanjut kalau kemarin check-in, reset kalau bolong
    last = e.get("last_checkin")
    if last:
        try:
            gap = (datetime.date.today() - datetime.date.fromisoformat(last)).days
        except ValueError:
            gap = 99
        e["streak"] = e.get("streak", 0) + 1 if gap == 1 else 1
    else:
        e["streak"] = 1
    e["last_checkin"] = today
    _save(e)
    print(f"OK: check-in '{a.slug}' tercatat. Streak {e['streak']} hari.")
    return 0


def _build_report(e):
    today = datetime.date.today().isoformat()
    tasks = e.get("daily_task", [])
    auto = [t for t in tasks if not e.get("wallet_sign")]
    manual = tasks if e.get("wallet_sign") else []
    lines = [f"📋 {e['name']} — {today}"]
    if e.get("chain") and e["chain"] != "—":
        lines[0] += f"  ({e['chain']})"
    if auto and not e.get("wallet_sign"):
        lines.append("✅ Task: " + "; ".join(auto))
    if manual:
        lines.append("⏰ Reminder (perlu wallet sign): " + "; ".join(manual))
    lines.append(f"TGE: {e.get('tge','—')}  |  Deadline: {e.get('deadline','—')}")
    lines.append(f"Streak: {e.get('streak',0)} hari")
    if e.get("link"):
        lines.append(e["link"])
    return "\n".join(lines)


def _send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "airdrop-manager"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read().decode("utf-8", "replace")


def cmd_report(a):
    e = _load(a.slug)
    if not e:
        print(f"NOT FOUND: {a.slug}")
        return 1
    text = _build_report(e)
    print(text)
    if a.send:
        tok, cid = e.get("bot_token"), e.get("bot_chat_id")
        if not tok or not cid:
            print("\n[WARN] --send diminta tapi bot_token/chat_id kosong. "
                  "Jalankan set-bot dulu.", file=sys.stderr)
            return 2
        try:
            st, body = _send_telegram(tok, cid, text)
            print(f"\n[SENT] Telegram HTTP {st}")
            if st != 200:
                print(body[:200], file=sys.stderr)
                return 3
        except Exception as ex:  # noqa: BLE001
            print(f"\n[ERROR] gagal kirim ke bot: {ex}", file=sys.stderr)
            return 3
    return 0


def cmd_list(_a):
    entries = _all_entries()
    if not entries:
        print("Registry kosong. Tambah dengan: add <slug> --name N --link URL")
        return 0
    print(f"{len(entries)} airdrop:")
    for e in entries:
        print(f"  [{e.get('delivery_mode','?')}] {e['slug']:<16} "
              f"{'+'.join(e.get('mechanic',[])) or '—':<28} "
              f"streak {e.get('streak',0)}  TGE {e.get('tge','—')}")
    return 0


def cmd_show(a):
    e = _load(a.slug)
    if not e:
        print(f"NOT FOUND: {a.slug}")
        return 1
    safe = dict(e)
    if safe.get("bot_token"):
        safe["bot_token"] = "***tersimpan***"  # jangan bocorkan token
    print(json.dumps(safe, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Registry airdrop untuk skill airdrop-manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("slug")
    a.add_argument("--name")
    a.add_argument("--link")
    a.add_argument("--chain")
    a.add_argument("--mechanic", help="comma-separated: checkin,wheel,bridge")
    a.add_argument("--task", help="semicolon-separated: 'buka app;klik checkin'")
    a.add_argument("--tge")
    a.add_argument("--reward")
    a.add_argument("--wallet-sign", action="store_true")
    a.add_argument("--deadline")
    a.add_argument("--mode", choices=["A", "B", "C"])
    a.set_defaults(func=cmd_add)

    b = sub.add_parser("set-bot")
    b.add_argument("slug")
    b.add_argument("--token", required=True)
    b.add_argument("--chat-id", required=True)
    b.set_defaults(func=cmd_set_bot)

    c = sub.add_parser("set-cron")
    c.add_argument("slug")
    c.add_argument("--job-id", required=True)
    c.set_defaults(func=cmd_set_cron)

    ci = sub.add_parser("checkin")
    ci.add_argument("slug")
    ci.set_defaults(func=cmd_checkin)

    r = sub.add_parser("report")
    r.add_argument("slug")
    r.add_argument("--send", action="store_true", help="POST laporan ke bot Telegram")
    r.set_defaults(func=cmd_report)

    ls = sub.add_parser("list")
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show")
    sh.add_argument("slug")
    sh.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
