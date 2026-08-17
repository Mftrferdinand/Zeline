"""ctfd_client.py — minimal CTFd API client (list, fetch, download, submit)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urljoin

import requests


@dataclass
class Challenge:
    id: int
    name: str
    category: str
    value: int
    solved: bool
    description: str = ""
    connection_info: str = ""
    files: list[str] = None  # absolute URLs


class CTFdClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _get(self, path: str, **kw):
        r = self.s.get(urljoin(self.base, path), timeout=self.timeout, **kw)
        r.raise_for_status()
        return r.json()

    def list_challenges(self) -> list[Challenge]:
        data = self._get("api/v1/challenges").get("data", [])
        out = []
        for c in data:
            out.append(Challenge(
                id=c["id"], name=c["name"], category=c.get("category", ""),
                value=c.get("value", 0), solved=c.get("solved_by_me", False),
            ))
        return out

    def get_challenge(self, cid: int) -> Challenge:
        c = self._get(f"api/v1/challenges/{cid}")["data"]
        files = [urljoin(self.base, f) for f in c.get("files", [])]
        return Challenge(
            id=c["id"], name=c["name"], category=c.get("category", ""),
            value=c.get("value", 0), solved=c.get("solved_by_me", False),
            description=c.get("description", ""),
            connection_info=c.get("connection_info", "") or "",
            files=files,
        )

    def download_files(self, chal: Challenge, dest_dir: str) -> list[str]:
        os.makedirs(dest_dir, exist_ok=True)
        saved = []
        for url in (chal.files or []):
            name = url.split("?")[0].split("/")[-1] or "file.bin"
            path = os.path.join(dest_dir, name)
            with self.s.get(url, stream=True, timeout=self.timeout) as r:
                r.raise_for_status()
                with open(path, "wb") as fh:
                    for chunk in r.iter_content(8192):
                        fh.write(chunk)
            saved.append(path)
        return saved

    def submit_flag(self, cid: int, flag: str) -> dict:
        """Returns {'status': 'correct'|'incorrect'|'already_solved'|..., 'message': str}."""
        r = self.s.post(
            urljoin(self.base, "api/v1/challenges/attempt"),
            json={"challenge_id": cid, "submission": flag},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        return {"status": data.get("status", "unknown"), "message": data.get("message", "")}
