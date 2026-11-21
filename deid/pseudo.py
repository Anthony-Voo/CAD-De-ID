"""Deterministic, same-length pseudonyms.

The same identity always maps to the same pseudonym (a keyed hash of its
case-folded form), so consistency across the whole corpus needs no shared
state between workers. Each pseudonym has exactly the byte length of the
original, so it can be substituted inside binary formats (OLE property sets,
Parasolid headers, IGES Hollerith strings, PDF dictionaries) without moving any
offsets. The secret key lives outside the submission (see README).
"""
import hashlib
import hmac
import os

_KEY = os.environ.get("DEID_KEY", "").encode() or b"dev-only-key-change-me"
_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"
_ALPHA = "abcdefghijklmnopqrstuvwxyz"

HOSTED_SUFFIXES = ("github.io", "gitlab.io", "files.wordpress.com", "wordpress.com", "blogspot.com",
                   "readthedocs.io", "sourceforge.net", "netlify.app", "herokuapp.com")

# Class prefixes give the pseudonym meaning ("this field held a person").
PREFIX = {"person": "Person", "org": "Org", "user": "user", "project": "Proj", "domain": "", "part": "PN"}


def _stream(key: str, n: int, alphabet: str) -> str:
    out, i = [], 0
    while len(out) < n:
        d = hmac.new(_KEY, f"{key}\x00{i}".encode(), hashlib.sha256).digest()
        out.extend(alphabet[b % len(alphabet)] for b in d)
        i += 1
    return "".join(out[:n])


def _match_case(src: str, dst: str) -> str:
    """Copy the original's letter case pattern so ALL-CAPS stays ALL-CAPS."""
    if src.isupper():
        return dst.upper()
    out = []
    for s, d in zip(src, dst):
        out.append(d.upper() if s.isupper() else d)
    return "".join(out)


def token(original: str, cls: str) -> str:
    """Same-length pseudonym for one identity token (no structure inside)."""
    n = len(original)
    key = f"{cls}:{original.casefold()}"
    pre = PREFIX.get(cls, "")
    if pre and n >= len(pre) + 3:
        body = pre + _stream(key, n - len(pre), _ALNUM)
    else:
        body = _stream(key, 1, _ALPHA) + _stream(key + "#", n - 1, _ALNUM) if n else ""
    # keep separators where they were, so "Dwayne Giardina" stays two words
    body = "".join(c if c in " ._-@/\\" else b for c, b in zip(original, body))
    return _match_case(original, body)


def domain(original: str) -> str:
    """Pseudonymize every label except the public suffix, keeping dots."""
    labels = original.split(".")
    low = original.casefold()
    keep = 1
    for suffix in HOSTED_SUFFIXES:  # owner.github.io: only the owner label identifies
        if low.endswith("." + suffix):
            keep = suffix.count(".") + 1
            break
    else:
        if len(labels) >= 3 and labels[-2] in ("co", "ac", "edu", "gov", "com", "org", "net"):
            keep = 2
    head = labels[: len(labels) - keep] or labels[:1]
    tail = labels[len(labels) - keep:] if len(labels) > keep else []
    fake = [token(l, "domain") if l else l for l in head]
    return ".".join(fake + tail)


def email(original: str) -> str:
    local, _, dom = original.partition("@")
    return token(local, "user") + "@" + domain(dom)


def pseudonym(original: str, cls: str) -> str:
    if cls == "domain":
        return domain(original)
    if cls == "email":
        return email(original)
    return token(original, cls)
