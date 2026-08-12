"""
tools/content.py — Social Content Scaffolder  (v4.1)

Helper deterministik buat sk27 (produksi sosial) & sk29 (riset/analitik/pipeline).
Yang STRUKTURAL (kalender, adaptasi platform, skeleton thread/carousel/reels,
repurpose) → dikerjain di sini, keyless. Yang butuh KREATIVITAS (isi kata)
→ skeleton + slot, diisi LLM (model_registry). Yang butuh DATA REAL (analitik,
best-time) → heuristik bertanda 'estimate', BUKAN angka karangan.

Stdlib only. Tanpa Date/random (deterministik biar reproducible).

Pakai:
    from content import calendar, adapt, thread, carousel, reels_script, repurpose
"""
from __future__ import annotations

from dataclasses import dataclass, field

# batas & sifat per platform (sumber: konvensi umum; sesuaikan)
PLATFORM = {
    "x":        {"max": 280,  "format": "thread/1-liner", "hook": "tweet-1", "tone": "cepat, opini"},
    "twitter":  {"max": 280,  "format": "thread/1-liner", "hook": "tweet-1", "tone": "cepat, opini"},
    "linkedin": {"max": 3000, "format": "story/thought-leadership", "hook": "baris-1", "tone": "profesional-personal"},
    "ig":       {"max": 2200, "format": "carousel/visual", "hook": "baris-1 + slide-1", "tone": "visual, relatable"},
    "instagram":{"max": 2200, "format": "carousel/visual", "hook": "baris-1 + slide-1", "tone": "visual, relatable"},
    "tiktok":   {"max": 150,  "format": "video-script", "hook": "3 detik pertama", "tone": "energetik, native"},
    "reels":    {"max": 150,  "format": "video-script", "hook": "3 detik pertama", "tone": "energetik, native"},
}

HOOK_STYLES = {
    "kontras":   'Semua bilang [X]. Mereka salah soal [topik].',
    "angka":     '[N] kesalahan [topik] yang diam-diam bikin lo rugi.',
    "pertanyaan":'Kenapa [hasil yang diinginkan] gak kejadian buat lo?',
    "stakes":    'Gua [kerugian konkret] sebelum sadar soal [topik].',
    "curiosity": 'Ini yang gak ada yang kasih tau soal [topik].',
}


def _plat(p: str) -> dict:
    return PLATFORM.get(p.lower(), {"max": 2000, "format": "post", "hook": "baris-1", "tone": "netral"})


# ───────────────── strategy / calendar ─────────────────
def calendar(pillars, days=30, per_week=5, platforms=None):
    """Generate slot kalender (deterministik, rotasi pillar×platform). Isi topik via LLM/sk29.

    Slot pakai index hari/minggu (bukan tanggal real — stamp tanggal di luar).
    """
    platforms = platforms or ["x"]
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][:max(1, min(7, per_week))]
    slots, i = [], 0
    weeks = max(1, days // 7)
    for w in range(weeks):
        for d in weekdays:
            slots.append({
                "slot": f"W{w+1}-{d}",
                "pillar": pillars[i % len(pillars)],
                "platform": platforms[i % len(platforms)],
                "format": _plat(platforms[i % len(platforms)])["format"],
                "topic": "<isi: ide dari sk29 trend research>",
            })
            i += 1
    return slots


def adapt(message: str, platforms):
    """Satu pesan → skeleton per platform (constraint + slot hook/CTA). Isi via LLM."""
    out = {}
    for p in platforms:
        meta = _plat(p)
        out[p] = {
            "core": message,
            "max_len": meta["max"],
            "format": meta["format"],
            "hook_location": meta["hook"],
            "tone": meta["tone"],
            "skeleton": f"[HOOK @ {meta['hook']}]\n[BODY: {message}]\n[CTA: satu aksi]",
        }
    return out


# ───────────────── pieces ─────────────────
def hook(style="curiosity", topic="[topik]"):
    tpl = HOOK_STYLES.get(style, HOOK_STYLES["curiosity"])
    return tpl.replace("[topik]", topic)


def caption(topic, platform="ig", hook_style="curiosity", cta="follow"):
    meta = _plat(platform)
    return (f"{hook(hook_style, topic)}\n\n[BODY — isi value soal {topic}, ≤{meta['max']} char, "
            f"tone {meta['tone']}]\n\nCTA: {cta}")


def hashtags(topic, platform="ig", mix=("broad", "niche", "branded")):
    """Bucket hashtag — broad/niche/branded. Isi nyata butuh riset trending (sk29)."""
    return {m: [f"#<{m}-{topic}-{k+1}>" for k in range(3)] for m in mix} | {
        "note": "isi nyata via trend research (sk29) — jangan spam tag generik"}


def thread(topic, n=7):
    out = [f"1/ HOOK: {hook('angka', topic)}"]
    for k in range(2, n):
        out.append(f"{k}/ POIN {k-1}: <satu ide soal {topic}, berdiri sendiri>")
    out.append(f"{n}/ CTA: <satu aksi + ringkas takeaway>")
    return out


def carousel(topic, slides=8):
    out = [f"Slide 1 — HOOK: {hook('pertanyaan', topic)}"]
    for k in range(2, slides):
        out.append(f"Slide {k} — <1 ide soal {topic}, 1 visual>")
    out.append(f"Slide {slides} — CTA + save/share")
    return out


def reels_script(topic, seconds=30):
    beats = [(0, 3, f"HOOK (3s pertama, wajib nyangkut): {hook('curiosity', topic)}"),
             (3, int(seconds * 0.4), f"SETUP: konteks {topic}"),
             (int(seconds * 0.4), int(seconds * 0.85), "VALUE: poin utama, on-screen text"),
             (int(seconds * 0.85), seconds, "CTA: follow/comment, 1 aksi")]
    return [{"t": f"{a}-{b}s", "voiceover": txt, "onscreen": "<teks layar>"} for a, b, txt in beats]


def repurpose(long_form: str, into=None):
    """1 long-form → banyak format (skeleton ter-adapt, bukan copy-paste)."""
    into = into or ["x_thread", "ig_carousel", "reels_script", "linkedin_post"]
    src = long_form.strip()
    head = src[:120] + ("…" if len(src) > 120 else "")
    builders = {
        "x_thread": lambda: thread("<dari long-form>", 7),
        "ig_carousel": lambda: carousel("<dari long-form>", 8),
        "reels_script": lambda: reels_script("<dari long-form>", 30),
        "linkedin_post": lambda: [f"HOOK: {head}", "STORY: <angle personal>", "TAKEAWAY + CTA"],
    }
    return {fmt: builders.get(fmt, lambda: [f"<adapt: {head}>"])() for fmt in into}


# ───────────────── analytics (butuh DATA REAL — jangan ngarang) ─────────────────
_POS = {"bagus", "mantap", "keren", "love", "great", "makasih", "thanks", "helpful", "👍", "🔥", "❤"}
_NEG = {"jelek", "buruk", "scam", "hate", "bad", "kecewa", "useless", "ribet", "👎", "😡"}


def analyze_comments(comments):
    """Heuristik sentimen/tema dari komentar real. Bukan ML — sinyal kasar buat triase."""
    pos = neg = neu = 0
    questions, quotes = [], []
    for c in comments:
        low = c.lower()
        p = sum(w in low for w in _POS)
        n = sum(w in low for w in _NEG)
        if "?" in c:
            questions.append(c)
        if p > n:
            pos += 1
        elif n > p:
            neg += 1; quotes.append(c)
        else:
            neu += 1
    total = max(1, len(comments))
    return {"sentiment": {"pos": pos, "neg": neg, "neu": neu, "pos_pct": round(pos / total * 100)},
            "questions": questions[:10], "negative_quotes": quotes[:10],
            "note": "heuristik kasar — pertanyaan = ide konten, kritik = objection buat di-address"}


def analyze_performance(posts):
    """Butuh metrik real per post (mis. 'engagement'). Tanpa itu → kasih kerangka, jangan ngarang."""
    have = [p for p in posts if isinstance(p, dict) and "engagement" in p]
    if not have:
        return {"error": "no real metrics", "framework": ["impressions", "engagement_rate", "CTR",
                "saves/shares", "follower_delta"], "note": "kasih data real per post — jangan estimasi angka"}
    ranked = sorted(have, key=lambda p: p["engagement"], reverse=True)
    return {"top": ranked[:3], "bottom": ranked[-3:],
            "note": "pola top → reinforce (sk55 instinct), bottom → drop/ubah. ubah 1 variabel/uji (sk56)"}


def ab_variants(text, n=3, vary=("hook", "cta")):
    """n variant, tiap variant ubah SATU elemen (prinsip sk56). Slot diisi LLM."""
    return [{"variant": chr(65 + i), "base": text, "change": vary[i % len(vary)],
             "skeleton": f"<{text} dengan {vary[i % len(vary)]} berbeda #{i+1}>"} for i in range(n)]


def best_time(audience_tz="UTC", history=None):
    """Best-time dari data audiens sendiri > benchmark. Tanpa history → benchmark + flag estimate."""
    if history:
        return {"source": "history", "tz": audience_tz,
                "note": "hitung dari distribusi engagement per jam di history (data real)"}
    return {"source": "benchmark-estimate", "tz": audience_tz,
            "ranges": {"x": ["08-10", "12-13", "17-19"], "linkedin": ["07-09", "12", "17-18"],
                       "ig": ["11-13", "19-21"], "tiktok": ["18-22"]},
            "note": "⚠️ estimate generik — ganti dengan data audiens nyata kalau ada"}


if __name__ == "__main__":
    print("calendar:", calendar(["edukasi", "proof", "opini"], days=7, per_week=3, platforms=["x", "ig"])[:3])
    print("thread:", thread("hook writing", 4))
    print("repurpose keys:", list(repurpose("long form content here").keys()))
    print("perf (no data):", analyze_performance([])["error"])
    print("best_time:", best_time("Asia/Jakarta")["source"])
