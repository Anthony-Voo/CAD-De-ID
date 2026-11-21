"""Deterministic, length-preserving pseudonyms.

The pseudonym of an identity depends only on (kind, normalized key) and on the
character classes of the text being replaced.  Letters map to letters (case of
the original kept), digits to digits, every other character is kept.  So
"Tom Kramer", "Tom_Kramer", "TomKramer" and "TOM KRAMER" all map to the same
letter sequence, and a pseudonym always has the same length as the original,
which lets us rewrite binary formats in place without moving any offsets.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata

SECRET = os.environ.get("DEID_SECRET", "machina-deid-v1").encode()

CONS = "bcdfghjklmnprstvwz"
VOWS = "aeiou"


def norm_key(s: str) -> str:
    """Identity key: lowercase alnum only, accents folded."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s.lower() if c.isalnum())


def _stream(kind: str, key: str, n: int, salt: bytes) -> bytes:
    out = b""
    i = 0
    while len(out) < n:
        out += hmac.new(SECRET, salt + kind.encode() + b"\0" + key.encode() + b"\0" + str(i).encode(), hashlib.sha256).digest()
        i += 1
    return out[:n]


_cache: dict = {}


def letters(kind: str, key: str, n: int) -> str:
    """Pronounceable letter stream (CV / CVC pattern) for an identity."""
    ck = (kind, key)
    have = _cache.get(ck, "")
    if len(have) >= n:
        return have[:n]
    raw = _stream(kind, key, max(n, 24) * 2, b"L")
    s = []
    vowel = raw[0] & 1 == 0
    for b in raw:
        if vowel:
            s.append(VOWS[b % len(VOWS)])
        else:
            s.append(CONS[b % len(CONS)])
        # mostly alternate, sometimes double consonant
        vowel = not vowel if (b & 7) else vowel
        if len(s) >= max(n, 24) * 2:
            break
    st = "".join(s)
    _cache[ck] = st
    return st[:n]


def digits(kind: str, key: str, n: int) -> str:
    raw = _stream(kind, key, max(n, 8), b"D")
    return "".join(str(b % 10) for b in raw)[:n]


def pseudo_text(kind: str, key: str, original: str) -> str:
    """Pseudonym with exactly len(original) characters, same char classes."""
    na = sum(1 for c in original if c.isalpha())
    nd = sum(1 for c in original if c.isdigit())
    L = letters(kind, key, na + 2)
    D = digits(kind, key, nd + 2)
    out = []
    li = di = 0
    for c in original:
        if c.isalpha():
            r = L[li]
            li += 1
            out.append(r.upper() if c.isupper() else r)
        elif c.isdigit():
            out.append(D[di])
            di += 1
        else:
            out.append(c)
    res = "".join(out)
    if res.lower() == original.lower() and original:
        # practically impossible; perturb deterministically
        res = res[:-1] + ("x" if res[-1] != "x" else "q")
    return res


def pseudo_bytes(kind: str, key: str, original: bytes, encoding: str) -> bytes:
    """Same-byte-length pseudonym for an encoded occurrence."""
    try:
        txt = original.decode(encoding)
    except UnicodeDecodeError:
        txt = original.decode("latin-1")
        encoding = "latin-1"
    # Map non-ascii letters to ascii first so the byte length shrinks, then pad
    p = pseudo_text(kind, key, "".join(c if ord(c) < 128 else ("a" if c.isalpha() else c) for c in txt))
    out = p.encode(encoding, "replace")
    if len(out) == len(original):
        return out
    # pad / trim with letters from the stream (only happens with non-ascii)
    unit = len("a".encode(encoding)) if encoding.lower().startswith("utf-16") else 1
    extra = letters(kind, key, 64)
    while len(out) < len(original):
        out += extra[(len(out) // unit) % 64].encode(encoding)
    return out[: len(original)]
