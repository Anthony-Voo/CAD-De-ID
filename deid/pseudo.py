"""Deterministic, length-preserving pseudonyms.

The pseudonym of an identity depends only on (kind, normalized key) and on the
character classes of the text being replaced.  Letters map to letters (case of
the original kept), digits to digits, every other character is kept.  So
"Ann Example", "Ann_Example", "AnnExample" and "ANN EXAMPLE" all map to the same
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
# normalized keys of every real identity: a pseudonym must never spell one
FORBIDDEN: set = set()


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
    """Pseudonym with exactly len(original) characters, same char classes.

    The kind is ignored on purpose: one key (e.g. "acme3d") must get one
    pseudonym whether it was seen as an account, an org or a project."""
    kind = "id"
    key = TEXT_SALT.get(key, key)
    na = sum(1 for c in original if c.isalpha())
    nd = sum(1 for c in original if c.isdigit())
    L = letters(kind, key, na + 2)
    D = digits(kind, key, nd + 2)
    out = []
    li = di = 0
    # canonical capitalization: first letter of every letter run upper case,
    # the rest lower case, whatever the case of this occurrence, so
    # "JDOE", "jdoe" and "Jdoe" all become the same string
    prev_alpha = False
    for c in original:
        if c.isalpha():
            r = L[li]
            li += 1
            out.append(r if prev_alpha else r.upper())
            prev_alpha = True
            continue
        prev_alpha = False
        if c.isdigit():
            out.append(D[di])
            di += 1
        else:
            out.append(c)
    res = "".join(out)
    tries = 0
    while FORBIDDEN and tries < 8 and _collides(res, key):
        tries += 1
        res = pseudo_text_salted(key + "#" + str(tries), original)
    if res.lower() == original.lower() and original:
        # practically impossible; perturb deterministically
        res = res[:-1] + ("x" if res[-1] != "x" else "q")
    return res


_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _collides(res: str, key: str) -> bool:
    """A pseudonym must not spell a real identity, as a whole or as one of
    its word pieces (split on punctuation and camelCase), e.g. "VibiCEB"."""
    whole = norm_key(res)
    if len(whole) >= 3 and whole in FORBIDDEN and whole != key:
        return True
    for t in _TOKEN_RE.findall(res):
        t = t.lower()
        if len(t) >= 3 and t in FORBIDDEN and t != key:
            return True
    return False


def pseudo_text_salted(key: str, original: str) -> str:
    na = sum(1 for c in original if c.isalpha())
    nd = sum(1 for c in original if c.isdigit())
    L = letters("id", key, na + 2)
    D = digits("id", key, nd + 2)
    out, li, di = [], 0, 0
    prev_alpha = False
    for c in original:
        if c.isalpha():
            out.append(L[li] if prev_alpha else L[li].upper()); li += 1
            prev_alpha = True
            continue
        prev_alpha = False
        if c.isdigit():
            out.append(D[di]); di += 1
        else:
            out.append(c)
    return "".join(out)


def pseudo_bytes(kind: str, key: str, original: bytes, encoding: str) -> bytes:
    """Same-byte-length pseudonym for an encoded occurrence."""
    kind = "id"
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


# ===================================================== entity-level pseudonyms
# Person names are pseudonymized token by token so every surface form of the
# same person agrees: a surname always maps to the same word, a given name's
# pseudonym always starts with PERM[its initial], and an initial maps to
# PERM[initial].  "Bea Sample", "B. Sample", "B.Sample", "SAMPLE, B."
# and "sample" therefore all use the same pseudonym pieces.  Organizations
# are pseudonymized word by word with legal suffixes kept, so "X Corporation",
# "X Corp." and "X Inc." share the base pseudonym.

_ALPHA = "abcdefghijklmnopqrstuvwxyz"
_PERM = None

ORG_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh",
                "mbh", "ag", "kg", "sa", "spa", "srl", "bv", "nv", "oy", "ab", "plc", "sas", "sarl", "pty",
                "kk", "the", "of", "and", "&", "de", "di", "für", "und", "et"}


def _perm():
    global _PERM
    if _PERM is None:
        letters = list(_ALPHA)
        raw = _stream("perm", "initials", 64, b"P")
        # Fisher-Yates with the keyed stream; must not map a letter to itself
        for i in range(len(letters) - 1, 0, -1):
            j = raw[i] % (i + 1)
            letters[i], letters[j] = letters[j], letters[i]
        for i in range(26):
            if letters[i] == _ALPHA[i]:
                k = (i + 1) % 26
                letters[i], letters[k] = letters[k], letters[i]
        _PERM = dict(zip(_ALPHA, letters))
    return _PERM


def _case_like(word_src: str, word_new: str) -> str:
    # canonical capitalization: first letter upper, rest lower
    return word_new[:1].upper() + word_new[1:].lower()


TOKEN_TABLE: dict = {}      # (namespace, key) -> pseudonym, filled by the registry (collision-free)
_TOKEN_USED: dict = {}      # pseudonym (lower) -> (namespace, key)


def _raw_token_pseudo(namespace: str, k: str, n: int, first_letter, salt: int) -> str:
    L = letters("id", namespace + ":" + k + ("" if not salt else "#%d" % salt), n + 4)
    s = "".join(L[i] for i in range(n))
    if first_letter:
        s = first_letter + s[1:]
    return s


def _token_pseudo(namespace: str, tok: str, first_letter: str | None = None) -> str:
    """Pseudonym for one alphabetic token (same length).  first_letter, if
    given, fixes the first character (used for given names).  Distinct
    tokens never share a pseudonym and a pseudonym never spells a real
    identity (rerolled deterministically)."""
    k = norm_key(tok)
    hit = TOKEN_TABLE.get((namespace, k, len(tok)))
    if hit is not None:
        return hit
    for salt in range(12):
        s = _raw_token_pseudo(namespace, k, len(tok), first_letter, salt)
        sl = s.lower()
        owner = _TOKEN_USED.get(sl)
        if sl != k and (owner is None or owner == (namespace, k)) and (len(sl) < 3 or sl not in FORBIDDEN):
            break
    _TOKEN_USED.setdefault(s.lower(), (namespace, k))
    TOKEN_TABLE[(namespace, k, len(tok))] = s
    return s


def build_token_table(person_names, org_names):
    """Assign every name token of the registry its pseudonym in a fixed
    (sorted) order, so the table - and the rerolls - are deterministic."""
    TOKEN_TABLE.clear()
    _TOKEN_USED.clear()
    perm = _perm()
    for name in sorted(set(person_names)):
        toks = [t for t in _TOK_SPLIT.findall(name) if t.isalpha() and len(t) > 1]
        for n, t in enumerate(toks):
            if n == 0 and len(toks) >= 2:
                _token_pseudo("given", t, perm.get(norm_key(t)[:1] or "x", "x"))
            else:
                _token_pseudo("sur", t)
    for name in sorted(set(org_names)):
        for t in _TOK_SPLIT.findall(name):
            if t.isalpha() and t.lower() not in ORG_SUFFIXES:
                _token_pseudo("org", t)


_TOK_SPLIT = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿ]+|\d+)")


def pseudo_person(original: str) -> str:
    """Person name -> pseudonym, same length, token-consistent (see above)."""
    parts = _TOK_SPLIT.split(original)
    toks = [(i, p) for i, p in enumerate(parts) if i % 2 == 1]
    alpha = [(i, p) for i, p in toks if p.isalpha()]
    perm = _perm()
    # "Surname, Given" ordering
    comma_first = len(alpha) >= 2 and any("," in parts[j] for j in range(alpha[0][0] + 1, alpha[1][0]))
    out = list(parts)
    for n, (i, p) in enumerate(toks):
        if p.isdigit():
            out[i] = digits("id", "num:" + p, len(p))
            continue
        if len(p) == 1:
            out[i] = perm.get(p.lower(), "x").upper()
            continue
        is_given = len(alpha) >= 2 and ((not comma_first and (i, p) == alpha[0]) or
                                        (comma_first and (i, p) != alpha[0] and (i, p) == alpha[1]))
        if is_given:
            fl = perm.get(norm_key(p)[:1] or "x", "x")
            out[i] = _case_like(p, _token_pseudo("given", p, fl))
        else:
            out[i] = _case_like(p, _token_pseudo("sur", p))
    return "".join(out)


def pseudo_org(original: str) -> str:
    parts = _TOK_SPLIT.split(original)
    out = list(parts)
    for i in range(1, len(parts), 2):
        p = parts[i]
        if p.isdigit():
            out[i] = digits("id", "num:" + p, len(p))
        elif p.lower() in ORG_SUFFIXES:
            out[i] = p
        else:
            out[i] = _case_like(p, _token_pseudo("org", p))
    res = "".join(out)
    if res.lower() == original.lower():
        res = pseudo_text("id", norm_key(original), original)
    return res


# person index for logins / e-mail local parts: built by the registry
PERSON_INDEX: dict = {}


def pseudo_login(original: str) -> str | None:
    """'bsample', 'sampleb', 'bea.sample', 'beas' -> composed from the
    person's pseudonym pieces, if the login matches a known person."""
    k = original.lower()
    hit = PERSON_INDEX.get(re.sub(r"[^a-z]", "", k))
    if not hit:
        return None
    given, sur, form = hit
    g = _token_pseudo("given", given, _perm().get(given[0], "x")).lower()
    s = _token_pseudo("sur", sur).lower()
    pieces = {"is": g[0] + s, "si": s + g[0], "gs": g + s, "g.s": g + "." + s, "g_s": g + "_" + s,
              "gi": g + s[0], "s": s, "g": g}[form]
    letters_only = [c for c in pieces if c.isalpha()]
    # keep the original's separators / digits / case layout
    out, li = [], 0
    for c in original:
        if c.isalpha():
            ch = letters_only[li] if li < len(letters_only) else "x"
            out.append(ch.upper() if c.isupper() else ch)
            li += 1
        else:
            out.append(c)
    res = "".join(out)
    return res if li == len(letters_only) else None


_GIVEN = None


def _given_names():
    global _GIVEN
    if _GIVEN is None:
        try:
            from .pii_detect import GIVEN
            _GIVEN = GIVEN
        except Exception:
            _GIVEN = set()
    return _GIVEN


def pseudo_domain(original: str) -> str:
    """Label by label, keeping www/com/edu/... (same rule as path renaming)."""
    from .seeds import DOMAIN_KEEP
    out = []
    for lab in original.split("."):
        if not lab or lab.lower() in DOMAIN_KEEP or lab.isdigit():
            out.append(lab)
        else:
            kl = KIND_OF.get(norm_key(lab))
            if kl in ("person", "org", "account") and lab.isalnum():
                v = pseudo_entity(kl, norm_key(lab), lab)
                out.append(v if len(v) == len(lab) else pseudo_text("id", norm_key(lab), lab))
            else:
                out.append(pseudo_text("id", norm_key(lab), lab))
    return ".".join(out)


def pseudo_email(original: str) -> str:
    if "@" not in original:
        return pseudo_text("id", norm_key(original), original)
    local, dom = original.split("@", 1)
    lp = pseudo_login(local) or pseudo_text("id", norm_key(local), local)
    return lp + "@" + pseudo_domain(dom)


TEXT_SALT: dict = {}  # key -> salted key, set where the plain hash collides
KIND_OF: dict = {}  # normalized key -> the one kind its pseudonym is built with
JOINED: dict = {}   # "3dwidgets" -> ("org", "3D Widgets"): joined forms of multi-word names


def _joined(original: str):
    """'3DWidgets' / 'AcmeMediaLab' / 'carlroe' written without separators:
    the pseudonym of the spaced name with its separators removed, in the
    original's case layout, so both spellings stay one identity."""
    hit = JOINED.get(original.lower())
    if not hit:
        return None
    k2, disp = hit
    full = pseudo_org(disp) if k2 == "org" else (pseudo_person(disp) if k2 == "person" else pseudo_text("id", norm_key(disp), disp))
    chars = [c for c in full if c.isalnum()]
    if len(chars) != len(original):
        return None
    if original.islower():
        return "".join(chars).lower()
    if original.isupper():
        return "".join(chars).upper()
    return "".join(chars)


def _fold(s: str) -> str:
    """'Núñez' -> 'Nunez' (one output character per input character)."""
    out = []
    for c in s:
        if c.isascii():
            out.append(c)
            continue
        b = "".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
        out.append(b if len(b) == 1 and b.isascii() else ("x" if c.isalpha() else c))
    return "".join(out)


def pseudo_entity(kind: str, key: str, original: str) -> str:
    """Kind-aware, same-length pseudonym (see the notes above)."""
    if not original.isascii():
        f = _fold(original)
        if len(f) == len(original) and f.isascii():
            original = f
    if kind in _NO_CANON:
        return _pseudo_entity_kind(kind, key, original)
    if original.isalnum():
        j = _joined(original)
        if j:
            return j
    if True:
        # one identity, one pseudonym: 'ASME' as an author field, as an
        # organization and as the label of asme.org all use the same rule
        k2 = KIND_OF.get(key)
        if k2 == "domain" and "." not in original and " " not in original:
            return pseudo_domain(original)
        if k2 in ("person", "org", "account"):
            kind = k2
    return _pseudo_entity_kind(kind, key, original)


_NO_CANON = {"domain", "email", "phone", "num", "literal"}


def _pseudo_entity_kind(kind: str, key: str, original: str) -> str:
    if kind == "person":
        if original.isalpha():
            joined = pseudo_login(original)      # "CarlRoe" -> pieces of "Carl Roe"
            if joined:
                return joined
        if " " not in original.strip() and "." not in original and "," not in original \
                and original.lower() in _given_names():
            fl = _perm().get(original[0].lower(), "x")
            return _case_like(original, _token_pseudo("given", original, fl))
        return pseudo_person(original)
    if kind == "org":
        return pseudo_org(original)
    if kind == "domain":
        return pseudo_domain(original)
    if kind == "email":
        return pseudo_email(original)
    if kind in ("account",):
        return pseudo_login(original) or pseudo_text(kind, key, original)
    return pseudo_text(kind, key, original)


def pseudo_entity_bytes(kind: str, key: str, original: bytes, encoding: str) -> bytes:
    if kind not in ("person", "org", "domain", "email", "account") and key not in KIND_OF and key not in JOINED:
        return pseudo_bytes(kind, key, original, encoding)
    try:
        txt = original.decode(encoding)
    except UnicodeDecodeError:
        return pseudo_bytes(kind, key, original, encoding)
    new = pseudo_entity(kind, key, txt)
    out = new.encode(encoding, "replace")
    if len(out) != len(original) and len(new) == len(txt):
        # keep accented letters where the original has them ('Jesús' ->
        # 'Seoñu'), so the byte length matches without padding and the
        # accent-folded pseudonym is the same as in UTF-16/ASCII contexts
        acc = {c: a for c, a in zip("aeiounc", "áéíóúñç")}
        acc.update({c.upper(): a.upper() for c, a in acc.items()})
        chars = list(new)
        used = set()
        for i, (oc, nc) in enumerate(zip(txt, new)):
            ob = len(oc.encode(encoding, "replace"))
            if ob == len(nc.encode(encoding, "replace")) or ob != 2:
                continue
            # this position, else the nearest accentable letter
            for j in sorted(range(len(chars)), key=lambda j: abs(j - i)):
                if j not in used and chars[j] in acc and txt[j].isascii() or j == i and chars[j] in acc:
                    chars[j] = acc[chars[j]]
                    used.add(j)
                    break
        out2 = "".join(chars).encode(encoding, "replace")
        if len(out2) == len(original):
            out = out2
    if len(out) != len(original):
        # non-ASCII letters (2 bytes in UTF-8) -> keep the canonical
        # pseudonym and pad it to the original byte length
        unit = 2 if encoding.lower().startswith("utf-16") else 1
        filler = letters("id", key, 64)
        while len(out) < len(original):
            out += filler[(len(out) // unit) % 64].encode(encoding)
        out = out[: len(original)]
    return out
