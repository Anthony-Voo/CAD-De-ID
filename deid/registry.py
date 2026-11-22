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

from .pseudo import norm_key, pseudo_bytes, pseudo_entity_bytes

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


def add_person_forms(ident, common=None, index=None):
    """Surface forms of one person identity (see Registry._add_person_forms)."""
    from .pii_detect import GIVEN
    from . import pseudo
    if common is None:
        try:
            from wordfreq import zipf_frequency

            def common(w):
                return max(zipf_frequency(w.lower(), l) for l in ("en", "fr", "de", "es", "it")) >= 3.0
        except ImportError:
            def common(w):
                return False
    disp = max(ident.variants, key=lambda v: (v.count(" "), len(v)))
    toks = [t for t in re.findall(r"[A-Za-z]+", disp) if len(t) > 1]
    single = re.fullmatch(r"([A-Z])\.?\s?([A-Za-z][A-Za-z'\-]+)", disp.strip())
    if len(toks) >= 2:
        g_, s_ = toks[0], toks[-1]
        ini = g_[0].upper()
    elif single:
        g_, s_ = None, single.group(2)
        ini = single.group(1).upper()
    else:
        return
    # only real-looking names get derived forms: junk "persons" such as
    # "ASME Downloaded From" or "Schaffner Group The" would otherwise yield
    # "From A" / "The S" and rewrite ordinary prose everywhere
    try:
        from wordfreq import zipf_frequency

        def very_common(w):
            return zipf_frequency(w.lower(), "en") >= 4.6
    except ImportError:
        def very_common(w):
            return w.lower() in {"the", "from", "this", "that", "with", "and", "for", "group", "inc", "of"}
    if len(toks) > 4 or any(very_common(t) and t.lower() not in GIVEN for t in toks):
        return
    if not re.fullmatch(r"[A-Za-z][A-Za-z'\-]+", s_) or len(s_) < 3:
        return
    gknown = bool(g_) and g_.lower() in GIVEN
    s_common = common(s_)
    if s_common and not gknown:
        return
    if g_ and not gknown and common(g_):
        return
    forms = {f"{ini}. {s_}", f"{ini}.{s_}", f"{ini}. {s_.upper()}", f"{ini}.{s_.upper()}"}
    if not s_common:
        forms |= {f"{ini} {s_}", f"{s_} {ini}", f"{s_}, {ini}."}
    if g_:
        forms.add(f"{s_}, {g_}")
    if len(s_) >= 5 and not common(s_) and s_.lower() not in GIVEN and (gknown or not g_):
        forms.add(s_)
    ident.variants |= forms
    if g_:
        gl, sl = g_.lower(), s_.lower()
        for form, login in (("is", gl[0] + sl), ("si", sl + gl[0]), ("gs", gl + sl), ("gi", gl + sl[0])):
            if len(login) >= 4:
                pseudo.PERSON_INDEX.setdefault(login, (gl, sl, form))


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
        r.build_entity_tables()
        return r

    def build_entity_tables(self):
        """Person index (logins -> person) and the collision-free token table."""
        from . import pseudo
        self._demote_junk_persons()
        persons, orgs = [], []
        idx = {}
        for (kind, key), ident in self.ids.items():
            if not ident.ctx:
                continue
            disp = max(ident.variants, key=lambda v: (v.count(" "), len(v))) if ident.variants else key
            if kind == "person":
                persons.append(disp)
                toks = [t for t in re.findall(r"[A-Za-z]+", disp) if len(t) > 1]
                if len(toks) >= 2:
                    g_, s_ = toks[0].lower(), toks[-1].lower()
                    for form, login in (("is", g_[0] + s_), ("si", s_ + g_[0]), ("gs", g_ + s_), ("gi", g_ + s_[0])):
                        if len(login) >= 4:
                            idx.setdefault(login, (g_, s_, form))
                else:
                    m1 = re.fullmatch(r"([A-Za-z])\.?\s?([A-Za-z]{4,})", disp.strip())
                    if m1:  # 'D. Bridges' -> login 'dbridges' / 'bridgesd'
                        g_, s_ = m1.group(1).lower(), m1.group(2).lower()
                        idx.setdefault(g_ + s_, (g_, s_, "is"))
                        idx.setdefault(s_ + g_, (g_, s_, "si"))
            elif kind == "org":
                orgs.append(disp)
        pseudo.PERSON_INDEX.clear()
        pseudo.PERSON_INDEX.update(idx)
        pseudo.JOINED.clear()
        for (kind, key), ident in self.ids.items():
            if kind not in ("org", "person") or not ident.ctx or not ident.variants:
                continue
            disp = max(ident.variants, key=lambda v: (v.count(" "), len(v)))
            words = re.findall(r"[A-Za-z0-9]+", disp)
            if len(words) >= 2 and " " in disp:
                j = "".join(words).lower()
                if len(j) >= 6 and j not in idx:
                    pseudo.JOINED.setdefault(j, (kind, disp))
        pseudo.build_token_table(persons, orgs)
        self._add_person_forms(idx)

    def _demote_junk_persons(self):
        """Person 'names' that are prose ('From Fig.', 'The Author', 'Holder.
        This', 'Someone else') stay field-only: they are still pseudonymized
        inside the author field they came from, never replaced as free text."""
        try:
            from wordfreq import zipf_frequency
        except ImportError:
            return
        from .pii_detect import GIVEN
        FUNC = {"the", "from", "this", "that", "of", "by", "and", "an", "as", "no", "not", "for",
                "only", "to", "in", "on", "at", "with", "is", "are", "be", "or"}
        for (kind, key), ident in self.ids.items():
            if kind != "person" or not ident.ctx or not ident.variants:
                continue
            disp = max(ident.variants, key=lambda v: (v.count(" "), len(v)))
            if not re.fullmatch(r"[A-Za-z .\-_']+", disp):
                continue
            toks = [t.lower() for t in re.findall(r"[A-Za-z]+", disp)]
            if len(toks) < 2 or any(len(t) == 1 for t in toks):
                continue
            ORGISH = {"inc", "corporation", "corp", "foundation", "university", "society", "companies", "company",
                      "software", "limited", "ltd", "lab", "labs", "institute", "systems", "instruments", "group",
                      "technology", "technologies", "llc", "gmbh", "authors", "consulting", "electronics", "semiconductor", "china"}
            if toks[-1] in ORGISH or len(toks) > 3:
                continue
            lead = toks[0] in FUNC and toks[0] not in ("and", "by")
            junk = lead or toks[-1] in FUNC
            if not junk and not any(t in GIVEN for t in toks):
                junk = all(zipf_frequency(t, "en") >= 4.3 for t in toks)
            if junk:
                ident.ctx = set() if isinstance(ident.ctx, set) else []

    def _add_person_forms(self, idx):
        """Every surface form of a known person is a variant of the same
        identity: initials ('C. Hollick', 'C.Hollick', 'Hollick C',
        'HOLLICK, C.'), the surname alone when distinctive, and the login
        forms built from the name ('chollick', 'hollickc', 'callumh')."""
        try:
            from wordfreq import zipf_frequency
        except ImportError:
            zipf_frequency = None
        from .pii_detect import GIVEN

        def common(w):
            if zipf_frequency is None:
                return False
            return max(zipf_frequency(w.lower(), l) for l in ("en", "fr", "de", "es", "it")) >= 3.0

        for (kind, key), ident in list(self.ids.items()):
            if kind != "person" or not ident.ctx:
                continue
            add_person_forms(ident, common)
        for login, (g_, s_, form) in idx.items():
            if len(login) >= 5 and not common(login) and ("account", login) not in self.ids:
                a = Identity("account", login, {login}, {"path", "text", "bin"}, False, 1)
                self.ids[("account", login)] = a
        self._auto = {}

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
            u16_only = False
            if ctx not in ident.ctx:
                # text-context identities still apply to binary data, but only
                # as UTF-16 text (how CAD programs store names in binaries)
                if not (ctx == "bin" and "text" in ident.ctx):
                    continue
                u16_only = True
            for v in ident.variants:
                nk = norm_key(v)
                if len(nk) < 3:
                    continue
                encs = ("utf-16-le",) if u16_only else ("utf-8", "utf-16-le")
                if ctx == "bin":
                    if len(v) < 5:
                        # short names in binary data only as UTF-16 text
                        # (6-8 specific bytes: random data never matches),
                        # for file-local values and for text-context identities
                        if not (ident.u16short or ("text" in ident.ctx and len(nk) >= 3)):
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
        # one automaton over lower-cased data: case-insensitive patterns as
        # they are, exact-case patterns verified against the original bytes
        merged = {}
        for ci, d in best.items():
            for pat, val in d.items():
                lp = pat.encode("latin-1").lower().decode("latin-1")
                merged.setdefault(lp, []).append((val, None if ci else pat.encode("latin-1")))
        if merged:
            A = ahocorasick.Automaton()
            for lp, vals in merged.items():
                A.add_word(lp, vals)
            A.make_automaton()
        else:
            A = None
        self._auto[ctx] = A
        return A

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
        A = self.automaton(ctx)
        cands = []
        if self.literals:
            L = self._literal_auto()
            if L:
                for end, val in L.iter(data.decode("latin-1")):
                    s, e = end - val[4] + 1, end + 1
                    if self._bounded(data, s, e, val[2], False):
                        cands.append((s, e, val))
        if A is not None:
            text = data.lower().decode("latin-1")
            for end, vals in A.iter(text):
                for val, exact in vals:
                    kind, key, enc, substr, ln = val
                    s, e = end - ln + 1, end + 1
                    if exact is not None and data[s:e] != exact:
                        continue
                    if self._bounded(data, s, e, enc, substr or (loose and len(key) >= 6)):
                        cands.append((s, e, val))
                        break
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
            buf[s:e] = pseudo_entity_bytes(kind, key, bytes(data[s:e]), enc)
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
