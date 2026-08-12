#!/usr/bin/env python3
"""
tools/video_pipeline.py — Video Script-to-Screen Pipeline  (v4.2, sk41)

Topik crypto → paket video utuh: script (hook/body/CTA dengan timing), storyboard
shot-by-shot, baris voiceover Bahasa Indonesia (untuk TTS), dan subtitle SRT.
Render aktual didelegasi ke skill `remotion_video`; TTS ke voice.py/general_tools.

Murni penyusun (offline, deterministik). Zero-dep (dataclasses).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VideoBrief:
    topic: str
    key_points: list                 # list[str]
    platform: str = "tiktok"         # tiktok | reels | youtube
    duration_sec: int = 45
    cta: str = "Follow buat update airdrop!"
    brand: str = "AirdropFinder"


@dataclass
class Segment:
    label: str                       # hook | point | cta
    start: float
    end: float
    voiceover: str
    on_screen: str
    shot: str                        # deskripsi visual buat storyboard

    @property
    def dur(self) -> float:
        return round(self.end - self.start, 2)


def _alloc(brief: VideoBrief):
    """Bagi durasi: hook 15%, CTA 15%, sisanya dibagi rata ke poin."""
    total = max(10, brief.duration_sec)
    hook = round(total * 0.15, 1)
    cta = round(total * 0.15, 1)
    body = total - hook - cta
    n = max(1, len(brief.key_points))
    per = round(body / n, 2)
    return hook, per, cta


def build_segments(brief: VideoBrief) -> list:
    hook_d, per, cta_d = _alloc(brief)
    segs = []
    t = 0.0
    # hook
    segs.append(Segment("hook", t, t + hook_d,
                        voiceover=f"Stop scroll! Ini soal {brief.topic}.",
                        on_screen=brief.topic.upper(),
                        shot="Close-up presenter / teks besar, motion cepat, sound effect whoosh"))
    t += hook_d
    # points
    for i, p in enumerate(brief.key_points, 1):
        segs.append(Segment("point", t, t + per,
                            voiceover=f"Nomor {i}: {p}.",
                            on_screen=f"{i}. {p}",
                            shot=f"B-roll terkait '{p}', overlay teks poin {i}, lower-third brand"))
        t += per
    # cta
    segs.append(Segment("cta", t, t + cta_d,
                        voiceover=brief.cta,
                        on_screen=f"@{brief.brand}",
                        shot="End-card logo + tombol follow animasi"))
    return segs


def _fmt_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list) -> str:
    """Subtitle format SRT dari voiceover tiap segmen."""
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_ts(s.start)} --> {_fmt_ts(s.end)}")
        lines.append(s.voiceover)
        lines.append("")
    return "\n".join(lines)


def build_package(brief: VideoBrief) -> dict:
    """Paket lengkap: script terstruktur, storyboard, voiceover, SRT, meta."""
    segs = build_segments(brief)
    return {
        "topic": brief.topic,
        "platform": brief.platform,
        "total_sec": round(segs[-1].end, 2),
        "script": [{"label": s.label, "start": s.start, "end": s.end,
                    "dur": s.dur, "voiceover": s.voiceover, "on_screen": s.on_screen}
                   for s in segs],
        "storyboard": [{"t": s.start, "shot": s.shot} for s in segs],
        "voiceover_lines": [s.voiceover for s in segs],
        "srt": to_srt(segs),
    }


if __name__ == "__main__":
    brief = VideoBrief(topic="3 Airdrop Base Worth Difarming",
                       key_points=["ZkProtoX points aktif", "BaseSwap volume tinggi",
                                   "Aerodrome LP gede"],
                       platform="tiktok", duration_sec=45)
    pkg = build_package(brief)
    print("total:", pkg["total_sec"], "s ·", len(pkg["script"]), "segmen")
    print(pkg["srt"])
