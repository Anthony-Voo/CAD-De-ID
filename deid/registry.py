"""Identity registry and the same-length replacement engine.

Every identity has a kind (person, org, account, domain, project, code), a
normalized key and a set of literal variants.  Matching runs on bytes so the
same code handles text files, binary CAD streams and paths; UTF-8 and UTF-16LE
encodings of every variant are searched.  Replacements always keep the byte
length, so offsets, record sizes and fixed-width columns never move.

Contexts:
  path - file and folder names (every identity)
  text - text formats and XML/metadata fields (keys of 3+ characters)
  bin  - raw binary streams (distinctive strings only: 5+ characters, a
         handful of exact case forms; case-insensitive only from 8 characters)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import ahocorasick

from .pseudo import norm_key, pseudo_bytes

SEPS = [" ", "_", "-", ".", ""]


@dataclass
class Identity:
    kind: str
    key: str
    variants: set = field(default_factory=set)
    ctx: set = field(default_factory=lambda: {"path", "text", "bin"})
    substr: bool = False  # may match inside a larger word
    sources: int = 0
    u16short: bool = False  # short value allowed in binary data, UTF-16 only


def _cls(b: int) -> str:
    if 65 <= b <= 90:
        return "U"
    if 97 <= b <= 122:
        return "L"
    if 48 <= b <= 57:
        return "D"
    return "O"


def _joins(a: str, b: str) -> bool:
    """True if character classes a (left) and b (right) belong to one word."""
    if a == "O" or b == "O":
        return False
    if a == "D" and b == "D":
        return True
    if a in "UL" and b in "UL":
        return not (a == "L" and b == "U")  # camelCase boundary
    return False  # letter/digit transitions count as boundaries


class Registry:
    def __init__(self):
        self.ids: dict[tuple, Identity] = {}
        self._auto: dict = {}
        self.literals: dict = {}  # exact original string -> exact replacement (renamed path parts)
        self._lit = None

    def set_literals(self, pairs: dict):
        self.literals = {k: v for k, v in pairs.items() if k != v and len(k) >= 6}
        self._lit = None

    def _literal_auto(self):
        if self._lit is None:
            A = ahocorasick.Automaton()
            n = 0
            for o, t in self.literals.items():
                for enc in ("utf-8", "utf-16-le"):
                    try:
                        ob, tb = o.encode(enc), t.encode(enc)
                    except UnicodeEncodeError:
                        continue
                    if len(ob) != len(tb):
                        continue
                    A.add_word(ob.decode("latin-1"), ("literal", tb, enc, False, len(ob)))
                    n += 1
            if n:
                A.make_automaton()
            self._lit = A if n else False
        return self._lit

    # ---------------------------------------------------------------- build
    def add(self, kind, text, ctx=None, substr=False, variants=None, key=None):
        text = (text or "").strip()
        k = key or norm_key(text)
        if len(k) < 2:
            return None
        ident = self.ids.get((kind, k))
        if ident is None:
            ident = Identity(kind, k)
            ident.ctx = set(ctx) if ctx is not None else {"path", "text", "bin"}
            self.ids[(kind, k)] = ident
        elif ctx is not None:
            ident.ctx |= set(ctx)
        # matching inside larger words only for long, distinctive keys
        ident.substr = (ident.substr or substr) and len(k) >= 6
        ident.sources += 1
        vs = set(variants or []) | {text}
        toks = [t for t in re.split(r"[\s_\-.]+", text) if t]
        if 1 < len(toks) <= 6:
            for s in SEPS:
                vs.add(s.join(toks))
        ident.variants |= {v for v in vs if len(norm_key(v)) >= 2}
        self._auto = {}
        return ident

    def to_json(self, path):
        data = [{"kind": i.kind, "key": i.key, "variants": sorted(i.variants), "ctx": sorted(i.ctx),
                 "substr": i.substr, "sources": i.sources} for i in self.ids.values()]
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)

    @classmethod
    def from_json(cls, path):
        r = cls()
        for d in json.load(open(path)):
            i = Identity(d["kind"], d["key"], set(d["variants"]), set(d["ctx"]), d["substr"] and len(d["key"]) >= 6, d.get("sources", 0))
            r.ids[(i.kind, i.key)] = i
        from . import pseudo
        pseudo.FORBIDDEN.update(k for (_, k) in r.ids)
        return r

    def remove(self, kind, key):
        self.ids.pop((kind, key), None)
        self._auto = {}

    # ------------------------------------------------------------- matching
    def automaton(self, ctx):
        """Two automatons per context: case-insensitive and exact-case."""
        if ctx in self._auto:
            return self._auto[ctx]
        best = {True: {}, False: {}}
        for ident in self.ids.values():
            if ctx not in ident.ctx:
                continue
            for v in ident.variants:
                nk = norm_key(v)
                if len(nk) < 3:
                    continue
                encs = ("utf-8", "utf-16-le")
                if ctx == "bin":
                    if len(v) < 5:
                        if not ident.u16short:
                            continue
                        encs = ("utf-16-le",)
                    ci = len(v) >= 8
                    forms = {v} if ci else {v, v.lower(), v.upper(), v.title()}
                elif ctx == "text":
                    if len(nk) < 3:
                        continue
                    forms, ci = {v}, True
                else:
                    forms, ci = {v}, True
                for f in forms:
                    for enc in (encs if ctx == "bin" else ("utf-8", "utf-16-le")):
                        try:
                            b = f.encode(enc)
                        except UnicodeEncodeError:
                            continue
                        pat = (b.lower() if ci else b).decode("latin-1")
                        val = (ident.kind, ident.key, enc, ident.substr, len(b))
                        prev = best[ci].get(pat)
                        if prev is None or len(ident.key) > len(prev[1]):
                            best[ci][pat] = val
        autos = {}
        for ci, d in best.items():
            if not d:
                autos[ci] = None
                continue
            A = ahocorasick.Automaton()
            for pat, val in d.items():
                A.add_word(pat, val)
            A.make_automaton()
            autos[ci] = A
        self._auto[ctx] = autos
        return autos

    @staticmethod
    def _bounded(data, s, e, enc, substr):
        if substr:
            return True
        step = 2 if enc == "utf-16-le" else 1
        cf, cl = _cls(data[s]), _cls(data[e - step])
        if s - step >= 0 and (step == 1 or data[s - 1] == 0):
            pc = _cls(data[s - step])
            if _joins(pc, cf):
                # "DRAWNBrit": an all-caps run followed by a capitalized word
                # (capital + lower case) is a word boundary (as in XMLParser)
                second = _cls(data[s + step]) if s + step < e else "O"
                if not (pc == "U" and cf == "U" and second == "L"):
                    return False
        if e + step <= len(data) and (step == 1 or data[e + 1] == 0):
            if _joins(cl, _cls(data[e])):
                return False
        return True

    def find(self, data: bytes, ctx: str, loose: bool = False):
        """Non-overlapping, leftmost-longest matches: list of (start, end, info).
        loose=True: identities of 6+ characters also match inside words
        (text whose word spaces are lost, e.g. glyph-by-glyph PDF text)."""
        if not data:
            return []
        autos = self.automaton(ctx)
        cands = []
        if self.literals:
            L = self._literal_auto()
            if L:
                for end, val in L.iter(data.decode("latin-1")):
                    s, e = end - val[4] + 1, end + 1
                    if self._bounded(data, s, e, val[2], False):
                        cands.append((s, e, val))
        for ci, A in autos.items():
            if A is None:
                continue
            text = (data.lower() if ci else data).decode("latin-1")
            for end, val in A.iter(text):
                kind, key, enc, substr, ln = val
                s, e = end - ln + 1, end + 1
                if self._bounded(data, s, e, enc, substr or (loose and len(key) >= 6)):
                    cands.append((s, e, val))
        if not cands:
            return []
        cands.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        out, last = [], -1
        for c in cands:
            if c[0] >= last:
                out.append(c)
                last = c[1]
        return out

    def replace(self, data: bytes, ctx: str, hits=None, loose: bool = False):
        ms = self.find(data, ctx, loose) if loose else self.find(data, ctx)
        if not ms:
            return data, 0
        buf = bytearray(data)
        for s, e, (kind, key, enc, substr, ln) in ms:
            if kind == "literal":
                buf[s:e] = key  # exact replacement bytes
                continue
            buf[s:e] = pseudo_bytes(kind, key, bytes(data[s:e]), enc)
            if hits is not None:
                hits.append((kind, key, bytes(data[s:e])))
        return bytes(buf), len(ms)

    def replace_str(self, s: str, ctx: str = "path") -> str:
        b, n = self.replace(s.encode("utf-8"), ctx)
        return b.decode("utf-8", "replace") if n else s


class Overlay(Registry):
    """Base registry plus a few per-file identities, without rebuilding the
    base automatons."""

    def __init__(self, base: Registry):
        super().__init__()
        self.base = base

    def find(self, data: bytes, ctx: str, loose: bool = False):
        a = self.base.find(data, ctx, loose)
        if not self.ids:
            return a
        b = Registry.find(self, data, ctx, loose)
        if not b:
            return a
        cands = sorted(a + b, key=lambda x: (x[0], -(x[1] - x[0])))
        out, last = [], -1
        for c in cands:
            if c[0] >= last:
                out.append(c)
                last = c[1]
        return out
