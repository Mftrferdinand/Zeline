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


WEB_TIMEOUT = 10
WEB_MAX_BYTES = 200_000
WEB_MAX_RESULTS = 5
_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Mobile Safari/537.36"


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


def _web_search(query: str) -> str:
    """Cari web via DuckDuckGo HTML; fallback Google News RSS."""
    query = query.strip()
    if not query:
        return "ERROR: query kosong."
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
        )
        if response.ok:
            results = []
            for match in re.finditer(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?(?:result__snippet"[^>]*>(.*?)</a>)?',
                response.text,
                re.S,
            ):
                url, title, snippet = match.group(1), match.group(2), match.group(3) or ""
                title = re.sub(r"(?s)<[^>]+>", "", title).strip()
                snippet = re.sub(r"(?s)<[^>]+>", "", snippet).strip()
                results.append(f"- {title}\n  {url}\n  {snippet[:300]}")
                if len(results) >= WEB_MAX_RESULTS:
                    break
            if results:
                return "\n".join(results)
    except requests.RequestException:
        pass
    # Fallback: Google News RSS tanpa API key.
    try:
        response = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "id", "gl": "ID", "ceid": "ID:id"},
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
        )
        if response.ok:
            root = ET.fromstring(response.content)
            entries = []
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = item.findtext("link") or ""
                if title:
                    entries.append(f"- {title}\n  {link}")
                if len(entries) >= WEB_MAX_RESULTS:
                    break
            if entries:
                return "\n".join(entries)
    except (requests.RequestException, ET.ParseError):
        pass
    return "ERROR: tidak dapat mencari web (pencarian gagal). Coba lagi nanti."


def _web_fetch(url: str) -> str:
    """Buka URL publik dan kembalikan teksnya. URL internal diblokir (SSRF)."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "ERROR: URL harus http/https yang valid."
    host = parsed.hostname or ""
    if not host or _is_internal_ip(host):
        return "ERROR: URL menunjuk ke alamat internal dan diblokir."
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


_RESULT_URL_RE = re.compile(r"https?://[^\s)]+")


def _search_result_urls(query: str, limit: int = 4) -> list[str]:
    """Ambil beberapa URL hasil pencarian untuk dibaca lebih dalam."""
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
        )
    except requests.RequestException:
        return []
    if not response.ok:
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'class="result__a"[^>]*href="([^"]+)"', response.text):
        raw = match.group(1)
        # DuckDuckGo membungkus URL asli di parameter uddg=.
        decoded = raw
        marker = "uddg="
        if marker in raw:
            from urllib.parse import unquote

            decoded = unquote(raw.split(marker, 1)[1].split("&", 1)[0])
        parsed = urlparse(decoded)
        host = parsed.hostname or ""
        if parsed.scheme not in ("http", "https") or not host or _is_internal_ip(host):
            continue
        if decoded in seen:
            continue
        seen.add(decoded)
        urls.append(decoded)
        if len(urls) >= limit:
            break
    return urls


def _deep_research(query: str) -> str:
    """Riset multi-sumber: cari, buka beberapa halaman teratas secara paralel,
    lalu rangkum poin-poin dari tiap sumber dengan sitasi URL.

    Berbeda dari ``web_search`` (5 judul mentah) dan ``web_fetch`` (1 URL),
    tool ini membaca beberapa sumber sekaligus (paralel, jauh lebih cepat)
    sehingga model bisa menyintesis jawaban berbukti tanpa banyak putaran tool.
    """
    query = query.strip()
    if not query:
        return "ERROR: query kosong."
    urls = _search_result_urls(query, limit=3)
    if not urls:
        # Fallback: hasil pencarian ringkas bila daftar URL gagal diambil.
        return _web_search(query)

    # Fetch paralel agar tidak menunggu tiap halaman berurutan (yang bikin lama).
    import concurrent.futures

    bodies: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_web_fetch, url): url for url in urls}
        for future in concurrent.futures.as_completed(futures, timeout=WEB_TIMEOUT + 5):
            url = futures[future]
            try:
                bodies[url] = future.result()
            except Exception:
                bodies[url] = "ERROR"

    sections: list[str] = [f"Riset untuk: {query}", ""]
    read = 0
    for url in urls:  # pertahankan urutan relevansi
        body = bodies.get(url, "ERROR")
        if body.startswith("ERROR") or body.startswith("(halaman"):
            continue
        excerpt = body[:1_800].strip()
        sections.append(f"### Sumber: {url}\n{excerpt}")
        sections.append("")
        read += 1
    if read == 0:
        return _web_search(query)
    sections.append(
        "Instruksi: sintesis poin-poin di atas menjadi jawaban ringkas & "
        "berbukti. Sebutkan sumber (URL) untuk klaim penting. Jangan mengarang "
        "fakta yang tidak ada di sumber. Jangan panggil tool lagi bila data ini "
        "sudah cukup menjawab."
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
        self._handlers: dict[str, ToolFunction] = {
            "runtime_info": self._runtime_info,
            "add_memory": self.memory.add,
            "remove_memory": self.memory.remove,
            "list_memory": self.memory.formatted,
            "load_skill": lambda name: skills.load_skill(name, include_private=self._can_read_private_skills),
            "web_search": lambda query: _web_search(query),
            "web_fetch": lambda url: _web_fetch(url),
            "deep_research": lambda query: _deep_research(query),
            "read_file": lambda path: _read_file(path, self.workspace),
            "write_file": lambda path, content: _write_file(path, content, self.workspace),
            "edit_file": lambda path, old_text, new_text: _edit_file(path, old_text, new_text, self.workspace),
            "patch_file": lambda path, old_text, new_text: _patch_file(path, old_text, new_text, self.workspace),
            "search_files": lambda query, pattern="*": _search_files(query, self.workspace, pattern),
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
        return [definition.schema() for definition in TOOL_DEFS if self.profile in definition.profiles]

    def run(self, name: str, args: dict[str, Any]) -> str:
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
