"""Tests for qr_code and render_chart tools + their modules."""
import sys
from pathlib import Path

import pytest

from zeline import tools


def _decode(png_path: Path) -> str:
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    img = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    assert img is not None
    det = cv2.QRCodeDetector()
    decoded, _, _ = det.detectAndDecode(img)
    return decoded


def test_qr_generates_and_scans(tmp_path: Path):
    out = tools._qr_code("https://github.com/natashaazhr", "qr.png", tmp_path)
    assert out.startswith("OK")
    assert _decode(tmp_path / "qr.png") == "https://github.com/natashaazhr"


def test_qr_wifi_payload(tmp_path: Path):
    payload = "WIFI:T:WPA;S:net;P:pass;;"
    assert tools._qr_code(payload, "wifi.png", tmp_path).startswith("OK")
    assert _decode(tmp_path / "wifi.png") == payload


def test_qr_rejects_bad_extension(tmp_path: Path):
    assert tools._qr_code("hi", "qr.txt", tmp_path).startswith("ERROR")


def test_qr_rejects_too_long(tmp_path: Path):
    assert tools._qr_code("x" * 400, "qr.png", tmp_path).startswith("ERROR")


def test_qr_rejects_empty(tmp_path: Path):
    assert tools._qr_code("", "qr.png", tmp_path).startswith("ERROR")


def test_chart_bar(tmp_path: Path):
    out = tools._render_chart("bar", "Mon=12, Tue=30, Wed=7", "chart.png", tmp_path, title="Sales")
    assert out.startswith("OK")
    png = (tmp_path / "chart.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000


def test_chart_line_and_pie(tmp_path: Path):
    assert tools._render_chart("line", "1=5, 2=8, 3=13", "l.png", tmp_path).startswith("OK")
    assert tools._render_chart("pie", "A=30, B=70", "p.png", tmp_path).startswith("OK")


def test_chart_rejects_bad_kind(tmp_path: Path):
    assert tools._render_chart("hologram", "a=1", "c.png", tmp_path).startswith("ERROR")


def test_chart_rejects_bad_values(tmp_path: Path):
    assert tools._render_chart("bar", "abc", "c.png", tmp_path).startswith("ERROR")
    assert tools._render_chart("bar", "", "c.png", tmp_path).startswith("ERROR")


def test_chart_too_many_points(tmp_path: Path):
    data = ", ".join(f"i{i}={i}" for i in range(30))
    assert tools._render_chart("bar", data, "c.png", tmp_path).startswith("ERROR")


def test_speech_requires_config(tmp_path: Path):
    from zeline import config
    saved_key, saved_url = config.API_KEY, config.BASE_URL
    try:
        config.API_KEY = ""
        out = tools._generate_speech("hello", "a.mp3", tmp_path)
        assert out.startswith("ERROR")
    finally:
        config.API_KEY, config.BASE_URL = saved_key, saved_url


def test_speech_rejects_bad_extension(tmp_path: Path):
    assert tools._generate_speech("hello", "a.txt", tmp_path).startswith("ERROR")


def test_speech_rejects_too_long(tmp_path: Path):
    assert tools._generate_speech("x" * 5000, "a.mp3", tmp_path).startswith("ERROR")


def test_video_requires_config(tmp_path: Path):
    from zeline import config
    saved_key, saved_url = config.API_KEY, config.BASE_URL
    try:
        config.API_KEY = ""
        out = tools._generate_video("a cat", "v.mp4", tmp_path)
        assert out.startswith("ERROR")
    finally:
        config.API_KEY, config.BASE_URL = saved_key, saved_url


def test_video_rejects_bad_extension(tmp_path: Path):
    assert tools._generate_video("a cat", "v.avi", tmp_path).startswith("ERROR")
