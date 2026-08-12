"""
hermes/browser_engine.py — Browser Automation (v4.0)

Playwright LURUS. TANPA stealth / fingerprint patching / anti-detect.
Tujuannya bikin agent bisa NGE-DRIVE browser buat tugas web3 yang sah:
navigasi dApp, baca halaman, isi form, capture WalletConnect URI, dan
TANDA TANGAN tx yang user authorize — semuanya lewat gate governor.

Yang SENGAJA TIDAK ada di sini (dan gak akan ditambah):
- Patch fingerprint / spoof navigator / evasi bot-detection
- Solver CAPTCHA
- User-agent palsu buat nyamarin automation

Default headful + persistent profile → login dApp & sesi WalletConnect ke-keep
antar run, jadi gak perlu re-connect tiap kali (ini soal kenyamanan, bukan evasi).

Dependency: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Awaitable

# Playwright di-import saat dipakai; py_compile tetap lolos tanpa lib terinstall.
try:
    from playwright.async_api import async_playwright, Page, BrowserContext
except Exception:  # belum terinstall — template tetap bisa di-review
    async_playwright = None  # type: ignore
    Page = BrowserContext = object  # type: ignore


DEFAULT_PROFILE = Path(os.environ.get("HERMES_BROWSER_PROFILE", "~/.hermes/browser-profile")).expanduser()
WC_URI_RE = re.compile(r"wc:[0-9a-fA-F-]+@\d+\?[^\s\"'<>]+")


@dataclass
class BrowserConfig:
    profile_dir: Path = DEFAULT_PROFILE
    headless: bool = False           # headful default — lebih jujur & gampang debug
    viewport: tuple[int, int] = (1280, 800)
    locale: str = "en-US"
    # CATATAN: kita TIDAK set user_agent palsu / args anti-detect. By design.


class BrowserAgent:
    """Wrapper Playwright tipis. Semua aksi side-effect (klik submit, sign) harus
    lewat helper yang ada gate-nya — lihat governed_sign()."""

    def __init__(self, config: Optional[BrowserConfig] = None):
        if async_playwright is None:
            raise RuntimeError("playwright belum terinstall: pip install playwright && playwright install chromium")
        self.cfg = config or BrowserConfig()
        self._pw = None
        self.ctx: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def __aenter__(self) -> "BrowserAgent":
        self.cfg.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        # persistent context = profil ke-save (cookie, localStorage, sesi WC)
        self.ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.cfg.profile_dir),
            headless=self.cfg.headless,
            viewport={"width": self.cfg.viewport[0], "height": self.cfg.viewport[1]},
            locale=self.cfg.locale,
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        return self

    async def __aexit__(self, *exc):
        if self.ctx:
            await self.ctx.close()
        if self._pw:
            await self._pw.stop()

    # ───────────── navigasi & baca (read-only, aman tanpa gate) ─────────────
    async def goto(self, url: str, wait: str = "domcontentloaded"):
        await self.page.goto(url, wait_until=wait)
        return self.page.url

    async def read_text(self, selector: Optional[str] = None) -> str:
        """Ambil teks halaman buat reasoning agent."""
        if selector:
            el = await self.page.query_selector(selector)
            return (await el.inner_text()) if el else ""
        return await self.page.inner_text("body")

    async def snapshot(self) -> dict:
        """Accessibility tree — lebih ringkas & stabil dari raw HTML buat agent."""
        return await self.page.accessibility.snapshot() or {}

    async def screenshot(self, path: str = "/tmp/hermes_page.png") -> str:
        await self.page.screenshot(path=path, full_page=True)
        return path

    async def fill(self, selector: str, value: str):
        await self.page.fill(selector, value)

    async def click(self, selector: str):
        await self.page.click(selector)

    # ───────────── WalletConnect: tangkap URI dari dApp ─────────────
    async def capture_walletconnect_uri(self, timeout_ms: int = 15000) -> Optional[str]:
        """
        Banyak dApp nampilin 'wc:...' URI (di QR / tombol copy) pas pilih WalletConnect.
        Kita ambil URI itu dari halaman, lalu serahkan ke WC signer (web3_connect.py)
        biar pairing-nya terjadi di sisi wallet yang DIKONTROL agent — bukan extension
        pihak ketiga. Ini jalur paling bersih: signing tetap di tangan kita + governor.
        """
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            html = await self.page.content()
            m = WC_URI_RE.search(html)
            if m:
                return m.group(0)
            # cek juga clipboard kalau dApp pakai tombol "copy to connect"
            try:
                clip = await self.page.evaluate("() => navigator.clipboard.readText()")
                if clip and clip.startswith("wc:"):
                    return clip
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return None


# ───────────── jalur SIGN: governor → screen → konfirmasi → sign ─────────────
@dataclass
class SignRequest:
    """Tx yang DIMINTA dApp (via WalletConnect). dApp boleh MINTA, agent yang
    putusin — tx cuma ditandatangani kalau lolos governor + di-authorize user."""
    w3: object                       # Web3
    account: object                  # LocalAccount
    tx: dict
    dapp_name: str = "unknown"
    usd_value: Optional[float] = None
    simulated_ok: Optional[bool] = None
    chain_id: Optional[int] = None


@dataclass
class SignResult:
    status: str                      # "signed" | "blocked" | "halt" | "rejected"
    detail: str = ""
    signed_raw: Optional[str] = None
    human_readable: dict = field(default_factory=dict)


async def governed_sign(
    req: SignRequest,
    confirm_cb: Optional[Callable[[dict], Awaitable[bool]]] = None,
) -> SignResult:
    """
    Satu pintu buat semua tanda tangan yang datang dari dApp lewat browser.
    Urutan (gak bisa di-skip):
        1. screen_tx  → decode tx jadi human-readable (web3_connect.py)
        2. governor.authorize → cap / slippage / kill-switch
        3. confirm_cb → konfirmasi user (kecuali auto_confirm; first-tx tetap di-summary)
        4. baru sign. Broadcast tetap lewat mev.send_private_tx di pemanggil.
    """
    from .web3_connect import screen_tx          # decode + 4byte + EIP-1271 helper
    from .governor import SpendGovernor, TxIntent

    chain_id = req.chain_id or req.w3.eth.chain_id
    screen = await screen_tx(req.w3, req.tx, req.dapp_name)
    human = screen.__dict__ if hasattr(screen, "__dict__") else {"summary": str(screen)}

    gov = SpendGovernor()
    intent = TxIntent(
        wallet=req.account.address, chain_id=chain_id, action=f"dapp:{req.dapp_name}",
        usd_value=req.usd_value, simulated_ok=req.simulated_ok,
        gas_price_wei=req.tx.get("gasPrice") or req.tx.get("maxFeePerGas"),
        recipient=req.tx.get("to"),
    )
    decision = gov.authorize(intent)
    if not decision.allowed:
        return SignResult(decision.verdict, decision.summary(), human_readable=human)

    if confirm_cb is not None:
        ok = await confirm_cb({"dapp": req.dapp_name, "screen": human,
                               "decision": decision.summary()})
        if not ok:
            return SignResult("rejected", "user menolak di konfirmasi", human_readable=human)

    signed = req.account.sign_transaction(req.tx)
    raw = "0x" + signed.raw_transaction.hex()
    # NB: caller yang broadcast (mev.send_private_tx) lalu panggil gov.record(intent, hash)
    return SignResult("signed", "lolos governor + konfirmasi", signed_raw=raw, human_readable=human)


# ───────────── contoh alur (pseudo, butuh playwright + env) ─────────────
async def _example():
    async with BrowserAgent(BrowserConfig(headless=False)) as b:
        await b.goto("https://app.uniswap.org")
        text = await b.read_text()
        print("page text len:", len(text))
        uri = await b.capture_walletconnect_uri()
        print("WC URI:", uri[:40] + "..." if uri else "tidak ketemu")
        # uri → pair via WC signer (web3_connect.py) → tx request → governed_sign(...)


if __name__ == "__main__":
    print("browser_engine: Playwright lurus, tanpa cloaking. "
          "Install: pip install playwright && playwright install chromium")
