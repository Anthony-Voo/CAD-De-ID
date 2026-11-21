"""Identity harvesting and the corpus-wide same-length scrubber.

Identities come from:
  * the corpus layout (manifest paths): GitHub/GitLab owners and projects, web
    domains, catalog project titles;
  * format headers harvested in a first pass (STEP FILE_NAME author/org, PDF
    authors, ...), added with `add()`;
and three rule-based detectors that need no list: e-mail addresses, URL hosts,
and user-profile folders inside paths (C:\\Users\\<name>, /home/<name>).

Every replacement is same-length (see pseudo.py) and whole-word, so it is safe
inside binary formats that store offsets.
"""
import json
import re

import ahocorasick

from .pseudo import pseudonym

try:
    from english_words import get_english_words_set
    _DICT = get_english_words_set(["web2", "gcide"], lower=True)
except Exception:  # pragma: no cover
    _DICT = set()

# Software, formats and CAD vocabulary: never identities in file content.
SOFTWARE = set("""
solidworks freecad inventor autodesk fusion fusion360 onshape openscad kicad eagle altium blender
sketchup rhino catia creo proe pro/e parasolid acis step iges stl dxf dwg pdf svg inkscape gimp
cairo cairographics tcpdf ghostscript acrobat adobe distiller microsoft windows linux macos apple
resources resource openframe opencascade occt salome meshlab cura prusa prusaslicer slic3r
ultimaker formlabs makerbot thingiverse grabcad github gitlab sourceforge python matlab octave
""".split())

# Not identifying: public hosting / mail providers / standards bodies. Kept as-is.
PUBLIC_DOMAINS = {
    "github.com", "gitlab.com", "githubusercontent.com", "raw.githubusercontent.com", "gmail.com",
    "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "me.com",
    "aol.com", "protonmail.com", "proton.me", "gmx.de", "gmx.net", "web.de", "qq.com", "163.com",
    "wordpress.com", "files.wordpress.com", "arxiv.org", "w3.org", "www.w3.org", "iso.org", "adobe.com",
    "ns.adobe.com", "purl.org", "openxmlformats.org", "schemas.openxmlformats.org", "microsoft.com",
    "schemas.microsoft.com", "inkscape.org", "www.inkscape.org", "sodipodi.sourceforge.net",
    "creativecommons.org", "freecad.org", "freecadweb.org", "solidworks.com", "autodesk.com",
    "ptc.com", "step.parts", "www.step.parts", "example.com", "example.org", "xmlns.com",
}

# Words too generic to treat as an identity even when they name a repo/owner.
STOP = set("""
cad files file parts part model models design designs project projects hardware hw source src
master main dev data docs doc drawings drawing assembly assemblies mechanical mech step stl
robot robotics lab labs open printer printed printing tools tool kit kits test tests demo
board case mount holder frame arm base box plate cover module modules system systems library
microscope centrifuge camera sensor sensors pump valve motor gear gears wheel wheels
""".split())

_WORD = re.compile(rb"[A-Za-z0-9]")
EMAIL = re.compile(rb"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:[a-z]{2}|com|org|net|edu|gov|mil|int|info|biz|io|co|ai|dev|app|tech|xyz|eu|asia|name|pro|me|tv|cc)(?![A-Za-z0-9-])")
URL_HOST = re.compile(rb"(?i)\b(?:https?|ftp)://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
USER_DIR = re.compile(rb"(?i)(?<=[\\/])(?:Users|home|Documents and Settings)[\\/]([^\\/\x00\r\n\"'<>|:*?]{2,40})(?=[\\/])")
USER_DIR16 = re.compile(rb"(?i)(?<=[\\/]\x00)(?:U\x00s\x00e\x00r\x00s\x00|h\x00o\x00m\x00e\x00)[\\/]\x00((?:[^\\/\x00]\x00){2,40})(?=[\\/]\x00)")
GENERIC_USERS = {"public", "default", "all users", "administrator", "admin", "user", "owner", "shared"}


def distinctive(name: str) -> bool:
    """Is a project/repo name specific enough to scrub from free text?"""
    n = name.strip()
    if len(n) < 5 or n.casefold() in STOP:
        return False
    parts = [p for p in re.split(r"[-_. ]+", n.casefold()) if p]
    if parts and all(p in STOP for p in parts):
        return False
    return bool(re.search(r"[0-9]", n) or re.search(r"[a-z][A-Z]", n) or len(n) >= 9)


class Identities:
    def __init__(self):
        self.items = {}  # casefolded original -> (original, cls)
        self.path_only = set()  # casefolded identities renamed in paths but not in content

    def add(self, s, cls, min_len=4):
        s = s.strip()
        if len(s) < min_len or s.casefold() in STOP or s.casefold() in GENERIC_USERS:
            return
        if cls == "domain" and (s.casefold() in PUBLIC_DOMAINS or "." not in s):
            return
        self.items.setdefault(s.casefold(), (s, cls))

    # ---------- harvest from the corpus layout
    def from_manifest(self, keys):
        for k in keys:
            p = k.split("/")
            if len(p) < 2:
                continue
            if p[0] == "archives" or (p[0] == "sources" and p[1] == "github" and len(p) > 2):
                top = p[1] if p[0] == "archives" else p[2]
                top = re.sub(r"\.zip$", "", top)
                bits = top.split("__")
                if len(bits) >= 2:
                    self.add(bits[0], "org", min_len=3)
                    if distinctive(bits[1]):
                        self.add(bits[1], "project")
            elif p[0] == "sources" and p[1] == "gitlab" and len(p) > 2:
                bits = [b for b in p[2].split("__")[1:] if b]
                for b in bits[:-1]:
                    if b not in ("project", "projects"):
                        self.add(b, "org", min_len=3)
                if bits and distinctive(bits[-1]):
                    self.add(bits[-1], "project")
            elif p[0] == "sources" and p[1] == "web" and len(p) > 2:
                self.add(p[2], "domain")
            elif p[0] == "sources" and p[1] == "catalog" and len(p) > 2:
                t = p[2]
                if "." in t and " " not in t and "_" not in t:
                    self.add(t, "domain")  # site folders such as data.nist.gov
        from .curated import CATALOG
        for text, cls, content in CATALOG:
            self.add(text, cls, min_len=3)
            if not content:
                self.path_only.add(text.casefold())

    def save(self, path):
        json.dump({"items": sorted(self.items.values()), "path_only": sorted(self.path_only)},
                  open(path, "w"), indent=0)

    def load(self, path):
        d = json.load(open(path))
        for s, cls in d["items"]:
            self.items.setdefault(s.casefold(), (s, cls))
        self.path_only |= set(d.get("path_only", []))
        return self


def content_ok(low: str, cls: str) -> bool:
    """May this identity be scrubbed from file CONTENT (not just paths)?"""
    if cls in ("domain", "email"):
        return low.casefold() not in PUBLIC_DOMAINS
    words = [w for w in re.split(r"[^a-z0-9]+", low) if w]
    if len(low) < 4 or low in SOFTWARE or low in STOP:
        return False
    if any(w in SOFTWARE for w in words):
        return False
    def common(w):
        return w in _DICT or (w.endswith("s") and w[:-1] in _DICT) or (w.endswith("es") and w[:-2] in _DICT)
    if len(words) == 1 and common(words[0]):
        return False
    if words and all(common(w) or w in STOP or w.isdigit() for w in words):
        return False
    return True


class Scrubber:
    """Same-length, whole-word replacement of every known identity in bytes."""

    def __init__(self, ids: Identities, for_paths: bool = False):
        self.A = ahocorasick.Automaton()
        for low, (orig, cls) in ids.items.items():
            if low in ids.path_only:
                if not for_paths:
                    continue
            elif not content_ok(low, cls):
                continue
            try:
                b = low.encode("latin-1")
            except UnicodeEncodeError:
                continue
            k8 = b.decode("latin-1")
            self.A.add_word(k8, (len(b), orig, cls, 1))
            k16 = b"".join(bytes([c, 0]) for c in b).decode("latin-1")
            self.A.add_word(k16, (len(b) * 2, orig, cls, 2))
        self.empty = len(self.A) == 0
        if not self.empty:
            self.A.make_automaton()
        self.hits = {}

    @staticmethod
    def _boundary(data: bytes, start: int, end: int, width: int) -> bool:
        before = data[start - width: start - width + 1] if start - width >= 0 else b""
        after = data[end: end + 1]
        return not (before and _WORD.match(before)) and not (after and _WORD.match(after))

    def _sub(self, data: bytes, spans):
        if not spans:
            return data
        out, last = bytearray(), 0
        for s, e, rep in sorted(spans):
            if s < last:
                continue  # overlapping: keep the first (longest wins, see below)
            out += data[last:s]
            out += rep
            last = e
        out += data[last:]
        return bytes(out)

    def scrub(self, data: bytes):
        """Return (new_bytes, number_of_replacements). Length is unchanged."""
        spans = []
        if not self.empty:
            low = data.lower().decode("latin-1")
            found = {}
            for end, (n, orig, cls, w) in self.A.iter(low):
                s = end + 1 - n
                if not self._boundary(data, s, end + 1, w):
                    continue
                if s not in found or found[s][0] < n:
                    found[s] = (n, orig, cls, w)
            for s, (n, orig, cls, w) in found.items():
                raw = data[s: s + n]
                text = raw.decode("latin-1") if w == 1 else raw[::2].decode("latin-1")
                rep = pseudonym(text, cls)
                b = rep.encode("latin-1", "replace")
                if w == 2:
                    b = b"".join(bytes([c, 0]) for c in b)
                if len(b) == n:
                    spans.append((s, s + n, b))
        spans += self._rules(data)
        new = self._sub(data, spans)
        assert len(new) == len(data)
        return new, len(spans)

    def _rules(self, data: bytes):
        spans = []
        for m in EMAIL.finditer(data):
            s = m.group().decode("latin-1")
            dom = s.partition("@")[2].casefold()
            rep = pseudonym(s, "email") if dom not in PUBLIC_DOMAINS else pseudonym(s.partition("@")[0], "user") + "@" + s.partition("@")[2]
            spans.append((m.start(), m.end(), rep.encode("latin-1")))
        for m in USER_DIR.finditer(data):
            u = m.group(1).decode("latin-1")
            if u.casefold() in GENERIC_USERS:
                continue
            spans.append((m.start(1), m.end(1), pseudonym(u, "user").encode("latin-1", "replace")))
        for m in USER_DIR16.finditer(data):
            u = m.group(1)[::2].decode("latin-1")
            if u.casefold() in GENERIC_USERS:
                continue
            rep = pseudonym(u, "user").encode("latin-1", "replace")
            spans.append((m.start(1), m.end(1), b"".join(bytes([c, 0]) for c in rep)))
        # keep only spans whose replacement has the exact original length
        return [(s, e, r) for s, e, r in spans if len(r) == e - s]

    def scrub_text(self, s: str) -> str:
        """For path components (str): same identities, any characters."""
        b = s.encode("utf-8")
        new, _ = self.scrub(b)
        return new.decode("utf-8", "replace")
