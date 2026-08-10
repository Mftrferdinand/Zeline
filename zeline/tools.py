"""Tool registry Zeline.

Prinsip penting untuk instalasi publik:

- ``safe``: memory percakapan + baca skill. Cocok untuk Telegram/WA/webhook.
- ``workspace``: safe + file read/write terbatas di workspace pemilik.
- ``full``: workspace + shell. Hanya default untuk CLI lokal pemilik.

Gateway publik *tidak pernah* mendapat shell/file tools tanpa owner secara
sengaja mengubah ``tool_profile`` di config. Ini mencegah orang yang chat bot
memakai LLM sebagai remote shell di device/VPS pemilik.
"""
from __future__ import annotations

import html as _html
import ipaddress
import json
import os
import re
import socket
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from zeline import config
from zeline import memory
from zeline import skills
from zeline import mcp as mcp_module

ToolFunction = Callable[..., str]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    profiles: frozenset[str]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


SAFE_PROFILES = {"safe", "workspace", "full"}


def _resolve_workspace_path(raw_path: str, workspace: Path) -> Path:
    """Resolve a relative/absolute user path and keep it inside workspace."""
    requested = Path(raw_path).expanduser()
    candidate = requested if requested.is_absolute() else workspace / requested
    # strict=False still resolves existing symlinks, so a symlink escape is blocked.
    resolved = candidate.resolve(strict=False)
    root = workspace.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path harus berada di workspace: {root}") from exc
    return resolved


def _read_file(path: str, workspace: Path) -> str:
    try:
        target = _resolve_workspace_path(path, workspace)
        if not target.is_file():
            return f"ERROR baca file: bukan file atau tidak ditemukan: {target}"
        content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > 20_000:
            content = content[:20_000] + "\n... [dipotong, file terlalu panjang]"
        return content
    except Exception as exc:
        return f"ERROR baca file: {exc}"


def _write_file(path: str, content: str, workspace: Path) -> str:
    try:
        if len(content) > 200_000:
            return "ERROR tulis file: konten terlalu besar (maksimum 200.000 karakter)."
        target = _resolve_workspace_path(path, workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"OK, {len(content)} karakter ditulis ke {target}"
    except Exception as exc:
        return f"ERROR tulis file: {exc}"


def _edit_file(path: str, old_text: str, new_text: str, workspace: Path) -> str:
    try:
        target = _resolve_workspace_path(path, workspace)
        content = target.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count != 1:
            return f"ERROR edit file: old_text harus unik (ditemukan {count})."
        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"OK, {target} diedit."
    except Exception as exc:
        return f"ERROR edit file: {exc}"


def _patch_file(path: str, old_text: str, new_text: str, workspace: Path) -> str:
    result = _edit_file(path, old_text, new_text, workspace)
    return result.replace("diedit", "dipatch")


def _update_task(task: str, status: str) -> str:
    allowed = {"pending", "in_progress", "completed", "cancelled"}
    clean_status = status.strip().lower()
    if clean_status not in allowed:
        return f"ERROR task: status harus salah satu {', '.join(sorted(allowed))}."
    clean_task = task.strip()[:500]
    if not clean_task:
        return "ERROR task: deskripsi kosong."
    return json.dumps({"task": clean_task, "status": clean_status}, ensure_ascii=False)


def _search_files(query: str, workspace: Path, pattern: str = "*") -> str:
    try:
        matches = []
        for target in workspace.rglob(pattern or "*"):
            if not target.is_file() or len(matches) >= 100:
                continue
            try:
                for line_number, line in enumerate(target.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if query.lower() in line.lower():
                        matches.append(f"{target.relative_to(workspace)}:{line_number}: {line[:300]}")
                        if len(matches) >= 100:
                            break
            except OSError:
                continue
        return "\n".join(matches) or "(tidak ada hasil)"
    except Exception as exc:
        return f"ERROR cari file: {exc}"


def _run_shell(command: str, workspace: Path) -> str:
    """Shell owner-only. Gateway tidak menerima profile ini secara default."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ},
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip() or "(tidak ada output)"
        if len(output) > 12_000:
            output = output[:12_000] + "\n... [output dipotong]"
        return f"exit={result.returncode}\n{output}"
    except subprocess.TimeoutExpired:
        return "ERROR: perintah timeout (>60 detik)"
    except Exception as exc:
        return f"ERROR jalankan perintah: {exc}"


def _execute_code(code: str, workspace: Path) -> str:
    """Jalankan snippet Python owner-only tanpa interpolasi shell."""
    if len(code) > 100_000:
        return "ERROR: kode terlalu panjang (maksimum 100.000 karakter)."
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [os.environ.get("PYTHON", "python"), "-c", code],
            cwd=str(workspace), capture_output=True, text=True, timeout=60,
            env={**os.environ},
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip() or "(tidak ada output)"
        return f"exit={result.returncode}\n{output[:12_000]}"
    except subprocess.TimeoutExpired:
        return "ERROR: kode timeout (>60 detik)"
    except Exception as exc:
        return f"ERROR jalankan kode: {exc}"


def _http_request(method: str, url: str, headers: str = "", body: str = "") -> str:
    """Panggil REST API dengan method bebas (GET/POST/PUT/PATCH/DELETE).

    Beda dari web_fetch (baca halaman): ini untuk memanggil API/webhook dengan
    header + body JSON. SSRF-protected: alamat internal diblokir setelah resolusi
    DNS, sama seperti web_fetch. Diinspirasi tool http_request awas-agent, ditulis
    ulang di Python dengan proteksi jaringan privat.
    """
    method = (method or "GET").strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return f"ERROR: method HTTP tidak didukung: {method}"
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "ERROR: URL harus http/https yang valid."
    host = parsed.hostname or ""
    if not host or _is_internal_ip(host):
        return "ERROR: URL menunjuk ke alamat internal dan diblokir."
    hdrs: dict[str, str] = {"User-Agent": _UA}
    if headers.strip():
        try:
            parsed_hdrs = json.loads(headers)
            if not isinstance(parsed_hdrs, dict):
                return "ERROR: headers harus objek JSON {\"Key\": \"Value\"}."
            hdrs.update({str(k): str(v) for k, v in parsed_hdrs.items()})
        except json.JSONDecodeError as exc:
            return f"ERROR: headers bukan JSON valid: {exc}"
    data = body.encode("utf-8") if body else None
    if data and "content-type" not in {k.lower() for k in hdrs}:
        stripped = body.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            hdrs["Content-Type"] = "application/json"
    try:
        response = requests.request(
            method, url, headers=hdrs, data=data, timeout=WEB_TIMEOUT, allow_redirects=True,
        )
    except requests.RequestException as exc:
        return f"ERROR request: {exc.__class__.__name__}: {exc}"
    text = response.text or ""
    if len(text) > 8_000:
        text = text[:8_000] + "\n... [dipotong]"
    ctype = response.headers.get("Content-Type", "")
    return f"Status: {response.status_code} {response.reason}\nContent-Type: {ctype}\n\n{text}".strip()


def _download_file(url: str, path: str, workspace: Path) -> str:
    """Unduh file (biner/teks) dari URL publik ke dalam workspace.

    SSRF-protected & path dikurung di dalam workspace. Diinspirasi tool
    download_file awas-agent. Berguna untuk ambil release/aset/dataset tanpa
    harus lewat run_shell curl.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "ERROR: URL harus http/https yang valid."
    host = parsed.hostname or ""
    if not host or _is_internal_ip(host):
        return "ERROR: URL menunjuk ke alamat internal dan diblokir."
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, headers={"User-Agent": _UA}, timeout=WEB_TIMEOUT, stream=True, allow_redirects=True) as response:
            if not response.ok:
                return f"ERROR: HTTP {response.status_code} {response.reason}."
            size = 0
            with open(dest, "wb") as handle:
                for chunk in response.iter_content(65536):
                    handle.write(chunk)
                    size += len(chunk)
                    if size > DOWNLOAD_MAX_BYTES:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        return f"ERROR: file melebihi batas {DOWNLOAD_MAX_BYTES // (1024*1024)} MB."
    except requests.RequestException as exc:
        return f"ERROR download: {exc.__class__.__name__}: {exc}"
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, terunduh: {rel} ({_format_size(size)})"


def _format_size(num_bytes: int) -> str:
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if num_bytes >= factor:
            return f"{num_bytes / factor:.1f} {unit}"
    return f"{num_bytes} B"


def _system_env() -> str:
    """Ringkasan lingkungan sistem: OS/arch, tool/runtime terpasang, port lokal aktif.

    Diinspirasi tool system_env awas-agent. Membantu model memutuskan perintah
    yang tersedia (python vs python3, ada node/git/docker?) sebelum menjalankannya.
    """
    import platform as _platform
    import shutil as _shutil

    lines = ["Lingkungan Sistem", ""]
    lines.append(f"- OS: {_platform.system()} {_platform.release()}")
    lines.append(f"- Arch: {_platform.machine()}")
    lines.append(f"- CPU: {os.cpu_count()} core")
    lines.append(f"- Python: {_platform.python_version()}")
    lines.append("")
    lines.append("Tool terpasang:")
    for tool in ("python", "python3", "pip", "node", "npm", "go", "gcc", "make", "git", "docker", "curl", "ffmpeg"):
        found = _shutil.which(tool)
        lines.append(f"- {tool}: {found or 'tidak ada'}")
    lines.append("")
    lines.append("Port lokal aktif (umum):")
    active = []
    for port in (22, 80, 443, 3000, 5000, 8000, 8080, 8081, 8089, 8092, 20128):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.05)
        try:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                active.append(port)
        finally:
            sock.close()
    lines.append("- " + (", ".join(str(p) for p in active) if active else "tidak ada yang terdeteksi"))
    return "\n".join(lines)


WEB_TIMEOUT = 12
# (connect, read) tuple — connect di-cap ketat agar tidak menggantung saat
# host lambat/diblokir; read sedikit lebih longgar untuk halaman besar.
SEARCH_TIMEOUT = (4, 6)
WEB_MAX_BYTES = 200_000
WEB_MAX_RESULTS = 5
DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50 MB cap untuk download_file
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
# Reader proxy: cepat & tahan blokir dari jaringan mobile/Termux (DuckDuckGo
# langsung sering timeout/HTTP 000). Semua pencarian & fetch lewat sini dulu.
_JINA_READER = "https://r.jina.ai/"


def _is_internal_ip(host: str) -> bool:
    """True jika hostname/IP menunjuk ke jaringan internal (proteksi SSRF)."""
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
        return _addr_is_internal(addr)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # DNS gagal = tidak boleh dicoba
    return all(_addr_is_internal(ipaddress.ip_address(info[4][0])) for info in infos)


def _addr_is_internal(addr: ipaddress._BaseAddress) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _parse_ddg_html(html_text: str) -> list[str]:
    """Ambil hasil (judul + url + snippet) dari HTML DuckDuckGo."""
    results: list[str] = []
    for match in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?(?:result__snippet"[^>]*>(.*?)</a>)?',
        html_text,
        re.S,
    ):
        url, title, snippet = match.group(1), match.group(2), match.group(3) or ""
        title = re.sub(r"(?s)<[^>]+>", "", title).strip()
        snippet = re.sub(r"(?s)<[^>]+>", "", snippet).strip()
        if title:
            results.append(f"- {title}\n  {snippet[:300]}")
        if len(results) >= WEB_MAX_RESULTS:
            break
    return results


def _search_gnews(query: str) -> list[tuple[str, str]]:
    """Google News RSS — paling andal & cepat dari Termux (200, <1s).
    Kembalikan [(judul, link)]."""
    try:
        response = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={"User-Agent": _UA},
            timeout=SEARCH_TIMEOUT,
        )
        if not response.ok:
            return []
        root = ET.fromstring(response.content)
        out: list[tuple[str, str]] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title:
                out.append((title, link))
            if len(out) >= WEB_MAX_RESULTS:
                break
        return out
    except (requests.RequestException, ET.ParseError):
        return []


def _search_wikipedia(query: str) -> list[tuple[str, str]]:
    """Wikipedia search API — cepat & stabil (200, <1s). Bagus untuk entitas."""
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query,
                    "format": "json", "srlimit": WEB_MAX_RESULTS},
            headers={"User-Agent": _UA},
            timeout=SEARCH_TIMEOUT,
        )
        if not response.ok:
            return []
        hits = response.json().get("query", {}).get("search", [])
        out: list[tuple[str, str]] = []
        for h in hits:
            title = h.get("title", "").strip()
            if title:
                url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
                snippet = re.sub(r"<[^>]+>", "", h.get("snippet", "")).strip()
                out.append((f"{title} — {snippet[:120]}" if snippet else title, url))
            if len(out) >= WEB_MAX_RESULTS:
                break
        return out
    except (requests.RequestException, ValueError):
        return []


def _search_jina_ddg(query: str) -> list[tuple[str, str]]:
    """DuckDuckGo via reader proxy. Cepat bila tidak kena 403; sering gagal."""
    from urllib.parse import quote, unquote
    try:
        response = requests.get(
            _JINA_READER + f"https://duckduckgo.com/html/?q={quote(query)}",
            headers={"User-Agent": _UA},
            timeout=SEARCH_TIMEOUT,
        )
        if not response.ok or not response.text.strip():
            return []
        out: list[tuple[str, str]] = []
        # Judul: '## [judul](link)'. URL asli DDG di parameter uddg=.
        for m in re.finditer(r"#+\s*\[([^\]]+)\]\(([^)]+)\)", response.text):
            title = m.group(1).strip()
            raw = m.group(2)
            url = unquote(raw.split("uddg=", 1)[1].split("&", 1)[0]) if "uddg=" in raw else raw
            if title and url.startswith("http"):
                out.append((title, url))
            if len(out) >= WEB_MAX_RESULTS:
                break
        return out
    except requests.RequestException:
        return []


def _web_search(query: str) -> str:
    """Cari web dari jaringan Termux (DuckDuckGo langsung mati/SSL-fail).
    Urutan andal: jina→DDG (bila hidup) → Google News RSS → Wikipedia.
    Selalu fail-fast; tidak pernah menggantung lama."""
    query = query.strip()
    if not query:
        return "ERROR: query kosong."
    for engine in (_search_jina_ddg, _search_gnews, _search_wikipedia):
        results = engine(query)
        if results:
            return "\n".join(f"- {title}" for title, _url in results)
    return "ERROR: tidak dapat mencari web (semua sumber gagal). Coba lagi nanti."


def _web_fetch(url: str) -> str:
    """Buka URL publik dan kembalikan teksnya. URL internal diblokir (SSRF).
    Utamakan reader proxy (cepat & tahan blokir); fallback fetch langsung."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "ERROR: URL harus http/https yang valid."
    host = parsed.hostname or ""
    if not host or _is_internal_ip(host):
        return "ERROR: URL menunjuk ke alamat internal dan diblokir."
    # 1) Reader proxy: mengembalikan teks/markdown bersih, jarang kena blokir.
    try:
        response = requests.get(
            _JINA_READER + url,
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
        )
        if response.ok and response.text.strip():
            text = response.text
            return text[:12_000] + ("\n... [dipotong]" if len(text) > 12_000 else "")
    except requests.RequestException:
        pass
    # 2) Fallback: fetch langsung.
    try:
        response = requests.get(
            url,
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        if not response.ok:
            return f"ERROR: HTTP {response.status_code}."
        chunks = []
        size = 0
        for chunk in response.iter_content(8192):
            chunks.append(chunk)
            size += len(chunk)
            if size > WEB_MAX_BYTES:
                break
        text = _html_to_text(b"".join(chunks))
        if not text:
            return "(halaman tidak memiliki teks yang bisa dibaca)"
        return text[:12_000] + ("\n... [dipotong]" if size > 12_000 else "")
    except requests.RequestException as exc:
        return f"ERROR fetch: {exc.__class__.__name__}."


def _search_result_urls(query: str, limit: int = 4) -> list[str]:
    """Kumpulkan URL hasil (untuk deep_research) dari sumber yang andal.
    Hanya URL yang benar-benar bisa di-fetch (bukan redirect Google News)."""
    urls: list[str] = []
    seen: set[str] = set()
    for engine in (_search_jina_ddg, _search_wikipedia):
        for _title, url in engine(query):
            if not url or not url.startswith("http"):
                continue
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if not host or _is_internal_ip(host):
                continue
            # Lewati proxy & redirect yang tidak bisa dibaca langsung.
            if any(bad in host for bad in ("jina.ai", "duckduckgo.com", "news.google.com")):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                return urls
        if urls:
            break
    return urls


def _deep_research(query: str) -> str:
    """Riset multi-sumber: cari URL teratas, baca 2-3 sumber PARALEL via reader
    proxy, lalu kumpulkan kutipan untuk disintesis. Dibatasi ketat agar cepat."""
    query = query.strip()
    if not query:
        return "ERROR: query kosong."
    urls = _search_result_urls(query, limit=3)
    if not urls:
        # Tidak dapat URL → pakai hasil web_search ringkas saja (cepat).
        return _web_search(query)

    import concurrent.futures

    bodies: dict[str, str] = {}
    # Batas total waktu keras agar tidak pernah menggantung lama.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_web_fetch, url): url for url in urls}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=14):
                url = futures[future]
                try:
                    bodies[url] = future.result()
                except Exception:
                    bodies[url] = "ERROR"
        except concurrent.futures.TimeoutError:
            pass  # ambil yang sudah selesai; sisanya dilewati

    sections: list[str] = [f"Riset untuk: {query}", ""]
    read = 0
    for url in urls:
        body = bodies.get(url, "")
        if not body or body.startswith("ERROR") or body.startswith("(halaman"):
            continue
        sections.append(f"### Sumber: {url}\n{body[:1_800].strip()}")
        sections.append("")
        read += 1
    if read == 0:
        return _web_search(query)
    sections.append(
        "Instruksi: sintesis poin-poin di atas menjadi jawaban ringkas & "
        "berbukti. Sebutkan sumber (URL) untuk klaim penting. Jangan mengarang "
        "fakta yang tidak ada di sumber. Jangan panggil tool lagi bila cukup."
    )
    return "\n".join(sections)[:14_000]


TOOL_DEFS: list[ToolDef] = [
    ToolDef(
        "runtime_info",
        "Tampilkan identitas runtime Zeline, model, provider, protokol, profile, dan tools tanpa membocorkan API key atau token.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "add_memory",
        "Simpan satu fakta jangka panjang tentang user di memory percakapan ini.",
        {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "Fakta singkat yang mau diingat"}},
            "required": ["fact"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "remove_memory",
        "Hapus fakta di memory percakapan ini yang mengandung potongan teks tertentu.",
        {
            "type": "object",
            "properties": {"substring": {"type": "string", "description": "Potongan teks fakta yang mau dihapus"}},
            "required": ["substring"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "list_memory",
        "Tampilkan semua fakta yang tersimpan untuk user/percakapan ini.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "load_skill",
        "Baca isi lengkap sebuah skill/prosedur berdasarkan nama file skill.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Nama skill tanpa .md"}},
            "required": ["name"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "web_search",
        "Cari informasi terbaru dari web (berita, artikel, data publik). Gunakan saat user minta info yang tidak kamu tahu atau butuh data terbaru.",
        {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Kata kunci pencarian"}},
            "required": ["query"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "web_fetch",
        "Buka satu URL publik (http/https) dan kembalikan teks halamannya. URL internal/jaringan privat otomatis diblokir.",
        {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL lengkap, misal https://example.com/artikel"}},
            "required": ["url"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "deep_research",
        "Riset mendalam multi-sumber: cari web, buka 3 halaman teratas, dan kumpulkan kutipan berbukti untuk disintesis. Gunakan saat user minta riset, perbandingan, atau jawaban yang butuh beberapa sumber—bukan sekadar 1 fakta cepat.",
        {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Topik atau pertanyaan riset"}},
            "required": ["query"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "http_request",
        "Panggil REST API/webhook dengan method bebas (GET/POST/PUT/PATCH/DELETE), header, dan body JSON. Beda dari web_fetch yang cuma baca halaman GET. Alamat jaringan internal otomatis diblokir.",
        {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "GET, POST, PUT, PATCH, DELETE"},
                "url": {"type": "string", "description": "URL endpoint http/https"},
                "headers": {"type": "string", "description": "Header sebagai JSON, mis. {\"Authorization\": \"Bearer x\"}. Opsional."},
                "body": {"type": "string", "description": "Body request (JSON/teks). Opsional."},
            },
            "required": ["method", "url"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "system_env",
        "Tampilkan info lingkungan: OS/arch/CPU, runtime & tool yang terpasang (python/node/go/git/docker/ffmpeg), dan port lokal aktif. Panggil sebelum menjalankan perintah untuk tahu tool apa yang tersedia.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "read_file",
        "Baca file teks di dalam workspace yang diizinkan.",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relatif di workspace"}},
            "required": ["path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "write_file",
        "Tulis/timpa file teks di dalam workspace yang diizinkan.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relatif di workspace"},
                "content": {"type": "string", "description": "Isi file"},
            },
            "required": ["path", "content"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "edit_file",
        "Edit satu bagian unik pada file teks di workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "patch_file",
        "Terapkan patch replace unik pada satu file workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "search_files",
        "Cari teks dalam file-file workspace.",
        {"type": "object", "properties": {"query": {"type": "string"}, "pattern": {"type": "string", "description": "Glob file, default *"}}, "required": ["query"]},
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "download_file",
        "Unduh file dari URL publik (http/https) ke dalam workspace. Untuk aset/release/dataset. Alamat internal diblokir; batas 50 MB.",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL file yang mau diunduh"},
                "path": {"type": "string", "description": "Path tujuan relatif di workspace"},
            },
            "required": ["url", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "update_task",
        "Laporkan perubahan status satu task coding. Panggil saat task mulai, selesai, dibatalkan, atau diganti.",
        {"type": "object", "properties": {"task": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]}}, "required": ["task", "status"]},
        frozenset({"full"}),
    ),
    ToolDef(
        "save_skill",
        "Simpan skill baru milik pemilik Zeline. Hanya gunakan bila pengguna lokal meminta prosedur reusable.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nama skill"},
                "content": {"type": "string", "description": "Markdown skill (# judul, > deskripsi, langkah)"},
            },
            "required": ["name", "content"],
        },
        frozenset({"full"}),
    ),
    ToolDef(
        "update_skill",
        "Patch satu bagian unik pada skill private milik operator.",
        {"type": "object", "properties": {"name": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["name", "old_text", "new_text"]},
        frozenset({"full"}),
    ),
    ToolDef(
        "execute_code",
        "Jalankan snippet Python di workspace operator dan kembalikan output nyata.",
        {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
        frozenset({"full"}),
    ),
    ToolDef(
        "run_shell",
        "Jalankan perintah shell di workspace pemilik. Hanya untuk operator lokal yang berwenang.",
        {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Perintah shell"}},
            "required": ["command"],
        },
        frozenset({"full"}),
    ),
]


class ToolExecutor:
    """Tool binding untuk satu session/identity dan satu security profile."""

    def __init__(self, identity: str, profile: str = "safe", workspace: str | Path | None = None):
        if profile not in SAFE_PROFILES:
            raise ValueError(f"tool profile tidak dikenal: {profile}")
        self.identity = identity or "cli:local"
        self.profile = profile
        self.workspace = Path(workspace or config.WORKSPACE).expanduser().resolve(strict=False)
        self.memory = memory.MemoryStore(self.identity)
        # Private skill hanya boleh dibaca operator local/full profile.
        self._can_read_private_skills = profile == "full"
        # MCP hanya untuk operator (workspace/full). Server stdio menjalankan
        # perintah lokal, jadi tidak boleh diekspos ke gateway publik (safe).
        self.mcp: mcp_module.MCPRegistry | None = None
        if profile in {"workspace", "full"} and getattr(config, "MCP_SERVERS", None):
            try:
                self.mcp = mcp_module.MCPRegistry.from_config({"mcp": {"servers": config.MCP_SERVERS}})
            except Exception:
                self.mcp = None
        self._handlers: dict[str, ToolFunction] = {
            "runtime_info": self._runtime_info,
            "add_memory": self.memory.add,
            "remove_memory": self.memory.remove,
            "list_memory": self.memory.formatted,
            "load_skill": lambda name: skills.load_skill(name, include_private=self._can_read_private_skills),
            "web_search": lambda query: _web_search(query),
            "web_fetch": lambda url: _web_fetch(url),
            "deep_research": lambda query: _deep_research(query),
            "http_request": lambda method, url, headers="", body="": _http_request(method, url, headers, body),
            "system_env": lambda: _system_env(),
            "read_file": lambda path: _read_file(path, self.workspace),
            "write_file": lambda path, content: _write_file(path, content, self.workspace),
            "edit_file": lambda path, old_text, new_text: _edit_file(path, old_text, new_text, self.workspace),
            "patch_file": lambda path, old_text, new_text: _patch_file(path, old_text, new_text, self.workspace),
            "search_files": lambda query, pattern="*": _search_files(query, self.workspace, pattern),
            "download_file": lambda url, path: _download_file(url, path, self.workspace),
            "update_task": _update_task,
            "save_skill": skills.save_skill,
            "update_skill": skills.update_skill,
            "execute_code": lambda code: _execute_code(code, self.workspace),
            "run_shell": lambda command: _run_shell(command, self.workspace),
        }

    def _runtime_info(self) -> str:
        available = [definition.name for definition in TOOL_DEFS if self.profile in definition.profiles]
        return json.dumps({
            "identity": config.NAME,
            "framework": "Zeline",
            "lab": "Zerolinear",
            "model": config.MODEL,
            "provider_base_url": config.BASE_URL,
            "protocol": config.PROTOCOL,
            "tool_profile": self.profile,
            "tools": available,
            "secrets": "API key dan token sengaja tidak ditampilkan",
        }, ensure_ascii=False, indent=2)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        native = [definition.schema() for definition in TOOL_DEFS if self.profile in definition.profiles]
        if self.mcp is not None:
            try:
                native.extend(self.mcp.schemas())
            except Exception:
                pass
        return native

    def run(self, name: str, args: dict[str, Any]) -> str:
        # Tool MCP di-dispatch ke registry (hanya untuk profile workspace/full).
        if self.mcp is not None and name.startswith(mcp_module.MCP_TOOL_PREFIX):
            if not self.mcp.has_tool(name):
                return f"ERROR: tool MCP '{name}' tidak terdaftar."
            return self.mcp.call(name, args)
        allowed = {definition.name for definition in TOOL_DEFS if self.profile in definition.profiles}
        if name not in allowed:
            return f"ERROR: tool '{name}' tidak diizinkan untuk profile '{self.profile}'."
        handler = self._handlers.get(name)
        if handler is None:
            return f"ERROR: tool '{name}' tidak tersedia."
        try:
            return str(handler(**args))
        except TypeError as exc:
            return f"ERROR argumen {name}: {exc}"
        except Exception as exc:
            return f"ERROR menjalankan {name}: {exc}"


# Backward-compatible aliases for kode kecil yang mungkin sudah import ini.
TOOLS = {}
TOOL_SCHEMAS = [definition.schema() for definition in TOOL_DEFS]
