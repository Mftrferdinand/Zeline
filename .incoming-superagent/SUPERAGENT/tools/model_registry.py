"""
tools/model_registry.py — Dynamic LLM Model Registry  (v4.0)

Nambah model LLM apa pun lewat satu perintah, langsung masuk cascade R7.
Dukung OpenAI-compatible (OpenRouter/DeepSeek/Groq/Together/Kimi/Ollama lokal/
LM Studio/vLLM/dll) dan Anthropic-style.

SECRET HYGIENE: API key disimpan TERENKRIPSI (scrypt+Fernet, key dari
HERMES_MASTER_PW) — gak pernah di-log/print mentah. list_models() ngasih key
ter-redact. Tanpa HERMES_MASTER_PW, registry NOLAK nyimpen key (gak ada plaintext
diam-diam). File ini FROZEN — self-improve gak boleh ngeditnya (nentuin ke mana
prompt/data dikirim + pegang key).

Command pattern (di chat):
    add model
    name: openrouter-llama
    api_key: sk-or-...
    base_url: https://openrouter.ai/api/v1
    model: meta-llama/llama-3.3-70b
    kind: openai            # openai | anthropic   (default openai)
    priority: 50            # makin kecil makin diutamakan di cascade

Dependency: httpx, cryptography.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.backends import default_backend

Kind = Literal["openai", "anthropic"]
DEFAULT_DB = Path(os.environ.get("HERMES_MODELS_DB", "~/.hermes/models.db")).expanduser()


@dataclass
class ModelConfig:
    name: str
    base_url: str
    model: str
    kind: str
    priority: int
    headers: dict
    api_key: Optional[str] = None   # cuma keisi pas get() internal; list() redacted


class ModelRegistry:
    def __init__(self, master_pw: Optional[str] = None, db_path: Path = DEFAULT_DB):
        self.master_pw = master_pw or os.environ.get("HERMES_MASTER_PW")
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS models (
            name TEXT PRIMARY KEY, base_url TEXT, model TEXT, kind TEXT,
            priority INTEGER DEFAULT 100, headers TEXT DEFAULT '{}', enc_key TEXT DEFAULT '')""")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        self.db.commit()

    # ---- crypto ----
    def _cipher(self) -> Optional[Fernet]:
        if not self.master_pw:
            return None
        row = self.db.execute("SELECT v FROM meta WHERE k='salt'").fetchone()
        if row:
            salt = base64.b64decode(row[0])
        else:
            salt = os.urandom(16)
            self.db.execute("INSERT INTO meta(k, v) VALUES('salt', ?)",
                            (base64.b64encode(salt).decode(),))
            self.db.commit()
        kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1, backend=default_backend())
        return Fernet(base64.urlsafe_b64encode(kdf.derive(self.master_pw.encode())))

    @staticmethod
    def _redact(key: str) -> str:
        if not key:
            return "(none)"
        return f"••••{key[-4:]}" if len(key) > 4 else "••••"

    # ---- manage ----
    def add_model(self, name: str, base_url: str, model: str,
                  api_key: str = "", kind: Kind = "openai",
                  priority: int = 100, headers: Optional[dict] = None) -> str:
        enc = ""
        if api_key:
            c = self._cipher()
            if c is None:
                raise RuntimeError(
                    "HERMES_MASTER_PW belum di-set — registry NOLAK nyimpen API key "
                    "tanpa enkripsi. Set master password dulu (secret hygiene).")
            enc = c.encrypt(api_key.encode()).decode()
        self.db.execute(
            "INSERT OR REPLACE INTO models(name, base_url, model, kind, priority, headers, enc_key) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, base_url, model, kind, priority, json.dumps(headers or {}), enc))
        self.db.commit()
        return f"model '{name}' ({kind}) ditambahkan · prioritas {priority} · key {self._redact(api_key)}"

    def list_models(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT name, base_url, model, kind, priority, enc_key FROM models ORDER BY priority").fetchall()
        out = []
        for name, base, mdl, kind, prio, enc in rows:
            out.append({"name": name, "base_url": base, "model": mdl, "kind": kind,
                        "priority": prio, "api_key": "set" if enc else "(none)"})
        return out

    def remove_model(self, name: str):
        self.db.execute("DELETE FROM models WHERE name = ?", (name,))
        self.db.commit()

    def get(self, name: str) -> Optional[ModelConfig]:
        r = self.db.execute(
            "SELECT name, base_url, model, kind, priority, headers, enc_key FROM models WHERE name=?",
            (name,)).fetchone()
        if not r:
            return None
        key = None
        if r[6]:
            c = self._cipher()
            key = c.decrypt(r[6].encode()).decode() if c else None
        return ModelConfig(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), key)

    def cascade(self) -> list[ModelConfig]:
        names = [row[0] for row in self.db.execute("SELECT name FROM models ORDER BY priority")]
        return [self.get(n) for n in names]

    # ---- dispatch ----
    def call(self, name: str, messages: list[dict], max_tokens: int = 1024) -> str:
        cfg = self.get(name)
        if cfg is None:
            raise ValueError(f"model '{name}' gak ada di registry")
        return _dispatch(cfg, messages, max_tokens)

    def call_with_cascade(self, messages: list[dict], max_tokens: int = 1024) -> tuple[str, str]:
        """Coba model sesuai prioritas, fallback ke berikutnya kalau gagal (R7).
        Return (jawaban, nama_model_yang_kepake)."""
        errs = []
        for cfg in self.cascade():
            try:
                return _dispatch(cfg, messages, max_tokens), cfg.name
            except Exception as e:
                errs.append(f"{cfg.name}: {e!r}")
        raise RuntimeError("semua model di cascade gagal:\n" + "\n".join(errs))


def _dispatch(cfg: ModelConfig, messages: list[dict], max_tokens: int) -> str:
    if cfg.kind == "anthropic":
        headers = {"x-api-key": cfg.api_key or "", "anthropic-version": "2023-06-01",
                   "content-type": "application/json", **cfg.headers}
        r = httpx.post(f"{cfg.base_url.rstrip('/')}/v1/messages", headers=headers, timeout=60,
                       json={"model": cfg.model, "max_tokens": max_tokens, "messages": messages})
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []))
    # default: OpenAI-compatible
    headers = {"Authorization": f"Bearer {cfg.api_key or ''}",
               "content-type": "application/json", **cfg.headers}
    r = httpx.post(f"{cfg.base_url.rstrip('/')}/chat/completions", headers=headers, timeout=60,
                   json={"model": cfg.model, "max_tokens": max_tokens, "messages": messages})
    data = r.json()
    return data["choices"][0]["message"]["content"]


if __name__ == "__main__":
    import tempfile
    os.environ["HERMES_MASTER_PW"] = "test-pw"
    reg = ModelRegistry(db_path=Path(tempfile.mktemp()))
    print(reg.add_model("openrouter-llama", "https://openrouter.ai/api/v1",
                        "meta-llama/llama-3.3-70b", api_key="sk-or-secret123", priority=50))
    print(reg.add_model("groq-fast", "https://api.groq.com/openai/v1",
                        "llama-3.1-8b-instant", api_key="gsk_abc999", priority=80))
    print("list (redacted):")
    for m in reg.list_models():
        print(" ", m)
    print("cascade order:", [c.name for c in reg.cascade()])
    print("decrypt roundtrip ok:", reg.get("openrouter-llama").api_key == "sk-or-secret123")
    # tanpa master pw → nolak simpen key
    reg2 = ModelRegistry(master_pw=None, db_path=Path(tempfile.mktemp()))
    try:
        reg2.add_model("x", "http://localhost:11434/v1", "qwen", api_key="secret")
    except RuntimeError as e:
        print("no-pw refusal works:", str(e)[:50], "...")
    # model lokal tanpa key (Ollama) → boleh tanpa pw
    print(reg2.add_model("ollama-local", "http://localhost:11434/v1", "qwen2.5", api_key=""))
