#!/usr/bin/env python3
"""
rsa_attacks.py — dispatcher for common CTF RSA weaknesses.

Pure-Python (gmpy2/pycryptodome) implementations of the attacks that don't need
Sage. For Coppersmith / lattice attacks, fall back to SageMath.

    pip install gmpy2 pycryptodome requests

Usage (programmatic):
    from rsa_attacks import (small_e_root, fermat_factor, wiener,
                             common_modulus, factordb, decrypt_with_factors)

Each returns the recovered plaintext as int (or None / factors), so the caller
can long_to_bytes() and validate.
"""
from __future__ import annotations

from math import gcd, isqrt

# gmpy2 is the fast path (big-number speed) and lives in the Docker sandbox.
# Fall back to pure-Python so this module also runs/tests on a vanilla host.
try:
    import gmpy2  # type: ignore

    def _iroot(n: int, e: int):
        r, exact = gmpy2.iroot(gmpy2.mpz(n), e)
        return int(r), bool(exact)

    def _invert(a: int, m: int) -> int:
        return int(gmpy2.invert(a, m))
except ImportError:  # pragma: no cover - exercised on hosts without gmpy2
    gmpy2 = None

    def _iroot(n: int, e: int):
        """Integer e-th root via binary search; returns (root, is_exact)."""
        if n < 0:
            raise ValueError("negative radicand")
        if n in (0, 1):
            return n, True
        lo, hi = 0, 1 << ((n.bit_length() + e - 1) // e + 1)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if mid ** e <= n:
                lo = mid
            else:
                hi = mid - 1
        return lo, lo ** e == n

    def _invert(a: int, m: int) -> int:
        return pow(a, -1, m)  # Python 3.8+

try:
    import requests
except ImportError:
    requests = None


def long_to_bytes(n: int) -> bytes:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b


# --- e=3 / small e: plaintext^e < n, take integer e-th root ------------------
def small_e_root(c: int, e: int = 3):
    root, exact = _iroot(c, e)
    return root if exact else None


# --- Fermat: p and q close together -----------------------------------------
def fermat_factor(n: int, max_iter: int = 1_000_000):
    a = isqrt(n)
    if a * a < n:
        a += 1
    for _ in range(max_iter):
        b2 = a * a - n
        b = isqrt(b2)
        if b * b == b2:
            return a - b, a + b
        a += 1
    return None


# --- Wiener: small private exponent d ---------------------------------------
def _continued_fraction(num, den):
    while den:
        q = num // den
        yield q
        num, den = den, num - q * den


def _convergents(cf):
    h0, h1, k0, k1 = 0, 1, 1, 0
    for a in cf:
        h0, h1 = h1, a * h1 + h0
        k0, k1 = k1, a * k1 + k0
        yield h1, k1


def wiener(e: int, n: int):
    for k, d in _convergents(_continued_fraction(e, n)):
        if k == 0:
            continue
        if (e * d - 1) % k != 0:
            continue
        phi = (e * d - 1) // k
        s = n - phi + 1
        disc = s * s - 4 * n
        if disc >= 0:
            r = isqrt(disc)
            if r * r == disc and (s + r) % 2 == 0:
                return d
    return None


# --- common modulus: same n, two coprime e ----------------------------------
def common_modulus(c1: int, c2: int, e1: int, e2: int, n: int):
    g, a, b = _egcd(e1, e2)
    if g != 1:
        return None
    if a < 0:
        c1 = _invert(c1, n); a = -a
    if b < 0:
        c2 = _invert(c2, n); b = -b
    return (pow(c1, a, n) * pow(c2, b, n)) % n


def _egcd(a, b):
    if b == 0:
        return a, 1, 0
    g, x, y = _egcd(b, a % b)
    return g, y, x - (a // b) * y


# --- factordb online lookup --------------------------------------------------
def factordb(n: int):
    if requests is None:
        return None
    try:
        r = requests.get("http://factordb.com/api", params={"query": str(n)}, timeout=10)
        data = r.json()
        if data.get("status") in ("FF", "CF", "P"):
            factors = []
            for f, mult in data["factors"]:
                factors.extend([int(f)] * int(mult))
            if len(factors) >= 2:
                return factors
    except Exception:
        return None
    return None


# --- given factors, decrypt --------------------------------------------------
def decrypt_with_factors(c: int, e: int, p: int, q: int):
    n = p * q
    phi = (p - 1) * (q - 1)
    d = _invert(e, phi)
    return pow(c, d, n)


def auto(c: int, e: int, n: int, extra: dict | None = None):
    """Try cheap attacks in order; return plaintext int or None."""
    extra = extra or {}
    # small e
    if e <= 5:
        m = small_e_root(c, e)
        if m and m**e < n:
            return m
    # factordb
    f = factordb(n)
    if f and len(f) == 2:
        return decrypt_with_factors(c, e, f[0], f[1])
    # fermat
    ff = fermat_factor(n, max_iter=200_000)
    if ff:
        return decrypt_with_factors(c, e, ff[0], ff[1])
    # wiener
    d = wiener(e, n)
    if d:
        return pow(c, d, n)
    return None


if __name__ == "__main__":
    print("rsa_attacks.py — import and call auto(c, e, n) or a specific attack.")
