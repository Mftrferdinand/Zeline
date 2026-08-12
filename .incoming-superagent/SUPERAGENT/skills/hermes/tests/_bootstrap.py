"""
Test bootstrap for the Hermes crypto scripts.

The production scripts depend on heavy 3rd-party packages (web3, eth_account,
httpx, solana, etc.) that are NOT required to test the pure, deterministic
logic in this suite. Because every script uses `from __future__ import
annotations`, the imported names are only referenced inside function bodies
(or as un-evaluated type hints), so a lightweight stub is enough to import
the modules and exercise their pure helpers offline with zero network and
zero external installs.

Usage (top of every test module):

    import _bootstrap  # noqa: F401  (installs stubs + sys.path)

It is import-safe to call multiple times.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import sys
import types


class _Any:
    """A universal placeholder: callable, subscriptable, attribute-transparent.

    Any attribute access, call, or item access returns the same object, so
    chains like ``Web3(Web3.HTTPProvider(url)).eth.get_block(...)`` resolve
    without raising. Tested code never relies on the *behaviour* of these
    stubs \u2014 only on being importable.
    """

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, name):
        return self

    def __getitem__(self, key):
        return self

    def __iter__(self):
        return iter(())


_ANY = _Any()


class _StubModule(types.ModuleType):
    def __getattr__(self, name):  # noqa: D401 - any symbol resolves to _ANY
        return _ANY


# Top-level packages we fabricate on demand (including all sub-modules).
_STUB_PREFIXES = (
    "web3",
    "eth_account",
    "eth_utils",
    "eth_keys",
    "eth_typing",
    "eth_hash",
    "ens",
    "httpx",
    "mnemonic",
    "solders",
    "solana",
    "base58",
    "bip_utils",
    "hyperliquid",
    "pysui",
    "aptos_sdk",
    "tonsdk",
    "pytoniq",
    "websockets",
)


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if top in _STUB_PREFIXES:
            return importlib.machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []  # mark as package so submodule imports resolve
        return mod

    def exec_module(self, module):  # nothing to execute
        return None


def install() -> None:
    if not any(isinstance(f, _StubFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _StubFinder())
    here = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.normpath(os.path.join(here, "..", "scripts"))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


install()
