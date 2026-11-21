"""Discovery: find identity values inside files.

harvest(path_in_corpus, data) -> list of (kind, value, where)

Sources: STEP/IGES/Parasolid/DXF headers, PDF Info + XMP, OLE property sets
(SolidWorks <2015, Inventor, Solid Edge), XML metadata in zip containers
(FreeCAD, 3MF, Fusion, SolidWorks 2015+ docProps), emails, URLs, user folders
in embedded paths, "DOMAIN\\user" logins and copyright lines.  Archives are
walked recursively.
"""
from __future__ import annotations

import io
import re
import zipfile

from . import formats

PLACEHOLDER = {
    "", "unknown", "author", "admin", "administrator", "user", "users", "operator", "owner", "default", "none",
    "test", "dummy", "anonymous", "nobody", "root", "guest", "public", "utilisateur", "benutzer", "usuario",
    "utente", "n/a", "na", "-", "--", "?", "x", "xx", "xxx", "name", "noname", "unnamed", "designer",
    "engineer", "company", "organization", "organisation", "your name", "your company", "student", "students",
    "open cascade", "opencascade", "open cascade sas", "spatial corp", "spatial", "spatial technology",
    "solidworks", "dassault systemes", "dassault systèmes", "autodesk", "autodesk, inc.", "ptc", "siemens",
    "freecad", "catia", "inventor", "solid edge", "onshape", "microsoft", "adobe", "pc", "home", "office",
    "cad", "draft", "drafter", "teacher", "lab", "machine", "computer", "windows user", "windows", "standard",
    "sdl", "gens", "mcad", "kicad", "inkscape", "user1", "user01", "usuario1", "default user",
}

SOFTWARE_RE = re.compile(
    r"(solid\s*works|autodesk|inventor|catia|creo|pro/?engineer|freecad|open\s*cascade|spatial|acis|parasolid|"
    r"siemens|nx\b|solid\s*edge|onshape|fusion|rhino|sketchup|microsoft|word|excel|powerpoint|acrobat|adobe|"
    r"distiller|pdf|matra|datavision|corel|zwcad|ansys|abaqus|comsol|mathworks|bentley|intergraph|graphisoft|bricsys|ghostscript|cairo|inkscape|libreoffice|openoffice|tex|latex|qt\b|altium|kicad|eagle|"
    r"step|iges|translator|processor|exporter|converter|version|build|\bsp\d|release|\d+\.\d+)",
    re.I,
)

EMAIL_DOM_RE = re.compile(rb"@((?:[A-Za-z0-9\-]{1,63}\.){1,6}[A-Za-z]{2,12})(?![A-Za-z0-9])")
LOCAL_TAIL_RE = re.compile(rb"[A-Za-z0-9._%+\-]{1,64}$")


class _M:
    def __init__(self, b):
        self.b = b

    def group(self, *a):
        return self.b


def _emails(data):
    for m in EMAIL_DOM_RE.finditer(data):
        s = m.start()
        lm = LOCAL_TAIL_RE.search(data, max(0, s - 64), s)
        if lm:
            yield _M(data[lm.start():m.end()])


class _EmailFinder:
    def finditer(self, data):
        return _emails(data)


EMAIL_RE = _EmailFinder()
URL_RE = re.compile(rb"(?:https?|ftp)://([A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,6})")
USERDIR_RE = re.compile(
    rb"(?:Users|USERS|users|Documents and Settings|home)[\\/]+([A-Za-z0-9._\- ]{2,40}?)(?=[\\/])",
)
LOGIN_RE = re.compile(rb"\b([A-Z0-9][A-Za-z0-9\-]{2,30})\\([A-Za-z][A-Za-z0-9._\-]{1,30})\b")
COPYRIGHT_RE = re.compile(rb"(?:Copyright|\(c\)|\xc2\xa9)\s*(?:\(c\)\s*)?(?:\d{4}(?:\s*[-,]\s*\d{4})?\s*,?\s*)?(?:by\s+)?([A-Z][A-Za-z.\-']+(?:\s+[A-Z][A-Za-z.\-']+){1,3})")
PARASOLID_RE = re.compile(rb"(?:SITE|USER|MC_ID)=([^;\r\n]{2,60});")
DXF_RE = re.compile(rb"\$(?:LASTSAVEDBY|AUTHOR)\r?\n\s*1\r?\n([^\r\n]{1,80})")
XML_FIELD_RE = re.compile(
    rb"<(?:[A-Za-z]+:)?(creator|lastModifiedBy|Author|Company|Manager|Designer|CreatedBy|LastModifiedBy|"
    rb"Owner|Publisher|rights|Copyright|Contributor)(?:\s[^>]*)?>\s*(?:<rdf:Seq>\s*<rdf:li[^>]*>|<rdf:Alt>\s*<rdf:li[^>]*>)?([^<]{1,120})<",
    re.I,
)
XML_ATTR_RE = re.compile(
    rb"<(?:Property|metadata|meta)\b[^>]*\bname=\"(CreatedBy|LastModifiedBy|Company|Designer|Author|Copyright|"
    rb"LicenseTerms|Application|Title)\"[^>]*>\s*(?:<String\s+value=\")?([^<\"]{1,120})",
    re.I,
)
FREECAD_PROP_RE = re.compile(rb"<Property name=\"(CreatedBy|LastModifiedBy|Company)\"[^>]*>\s*<String value=\"([^\"]{1,120})\"")

NS_DOMAINS = re.compile(
    r"(w3\.org|adobe\.com|purl\.org|openxmlformats\.org|microsoft\.com|schemas\.|xmlsoap|iptc\.org|"
    r"inkscape\.org|sodipodi|creativecommons\.org|apache\.org|gnu\.org|ietf\.org|iso\.org|example\.|"
    r"google\.com|googleapis\.com|gstatic|pki\.goog|digicert|verisign|thawte|globalsign|ns\.|xmlns|"
    r"solidworks\.com|autodesk\.com|freecad|opencascade|spatial\.com|steptools\.com|ptc\.com|siemens\.com|"
    r"dassault|3ds\.com|onshape\.com|cairographics|libreoffice|openoffice|github\.com|gitlab\.com|"
    r"wikipedia\.org|youtube\.com|python\.org|mozilla|apple\.com|java\.com|oracle\.com|sun\.com|"
    r"sourceforge\.net|amazon\.com|cloudfront\.net|dropbox|wordpress\.com|wp\.com|jquery|bootstrap|"
    r"fonts\.|cdn\.|localhost)",
    re.I,
)


def clean(v: str) -> str:
    v = v.replace("\x00", "").strip().strip("'\"()[]{};,")
    return re.sub(r"\s+", " ", v)


def is_placeholder(v: str) -> bool:
    lo = v.lower().strip()
    if lo in PLACEHOLDER or len(re.sub(r"[^A-Za-z]", "", lo)) < 2:
        return True
    if re.fullmatch(r"[\d\W_]+", lo):
        return True
    if re.fullmatch(r"(user|usuario|admin|pc|desktop|laptop|win)[\-_ ]?\d*", lo):
        return True
    return False


def person_values(v: str):
    """Split a field value into identity strings (handles 'DOM\\user (DOM)')."""
    v = clean(v)
    out = []
    m = re.fullmatch(r"([A-Za-z0-9_\-]+)\\([^ ()\\]+)(?:\s*\(.*)?", v)
    if m:
        dom, user = m.groups()
        if not is_placeholder(user):
            out.append(("account", user))
        if not re.fullmatch(r"(DESKTOP|LAPTOP|PC|WIN|USER)-?[A-Z0-9]*", dom, re.I) and not is_placeholder(dom):
            out.append(("org", dom))
        else:
            out.append(("host", dom))
        return out
    for part in re.split(r"\s*(?:;|,|/| and | & |\|)\s*", v):
        part = clean(part)
        if part and not is_placeholder(part) and not SOFTWARE_RE.search(part) and len(part) <= 60:
            out.append(("person", part))
    return out


def org_values(v: str):
    v = clean(v)
    if not v or is_placeholder(v) or SOFTWARE_RE.search(v) or len(v) > 80:
        return []
    return [("org", v)]


def harvest_text(data: bytes, where: str):
    out = []
    for m in EMAIL_RE.finditer(data):
        e = m.group().decode("latin-1").lstrip("-._")
        local, dom = e.split("@", 1)
        if not (dom == dom.lower() or dom.split(".")[-1] == dom.split(".")[-1].upper() == dom.split(".")[-1] and dom[0].isalpha() and len(dom.split(".")[-2]) >= 3):
            continue
        if len(local) < 2 or re.fullmatch(r"[A-Za-z0-9]{1,6}", local) and not re.fullmatch(r"[a-z]+|[A-Z][a-z]+", local):
            continue
        if re.search(r"\.(png|jpg|gif|svg|tlb|dll)$", e, re.I) or NS_DOMAINS.search(dom):
            continue
        if not re.search(r"[A-Za-z]{2}", local) or not re.fullmatch(r"[a-z0-9.\-]+", dom.lower()):
            continue
        # random binary noise rarely produces a known TLD with lowercase labels
        if not re.fullmatch(r"[a-z0-9.\-]+\.(com|org|net|edu|gov|io|de|fr|it|es|uk|nl|ch|at|be|cn|jp|ru|in|ca|au|us|eu|dk|se|no|fi|pl|cz|pt|br|mx|ar|tr|kr|tw|hk|sg|il|ir|gr|hu|ro|si|sk|lu|ie|nz|za|info|biz|co|me|dev|ac\.\w+)", dom.lower()):
            continue
        out.append(("email", e, where))
    for m in URL_RE.finditer(data):
        d = m.group(1).decode("latin-1").lower()
        if not NS_DOMAINS.search(d) and re.search(r"\.[a-z]{2,}$", d):
            out.append(("domain", d, where))
    for m in USERDIR_RE.finditer(data):
        u = clean(m.group(1).decode("latin-1"))
        if not is_placeholder(u) and u.lower() not in ("public", "all users", "default user", "shared"):
            out.append(("account", u, where))
    for m in (LOGIN_RE.finditer(data) if (b"DESKTOP-" in data or b"LAPTOP-" in data) else ()):
        dom, user = (x.decode("latin-1") for x in m.groups())
        if re.fullmatch(r"(DESKTOP|LAPTOP|PC|WIN)-[A-Z0-9]{4,}", dom) and not is_placeholder(user):
            out.append(("account", user, where))
            out.append(("host", dom, where))
    for m in COPYRIGHT_RE.finditer(data):
        v = clean(m.group(1).decode("latin-1"))
        if not SOFTWARE_RE.search(v) and not is_placeholder(v) and not re.search(r"(All|Rights|Reserved|Inc$)", v):
            out.append(("person", v, where))
    for m in PARASOLID_RE.finditer(data[:4096]):
        v = clean(m.group(1).decode("latin-1"))
        if not is_placeholder(v):
            out.append(("account", v, where))
    for m in DXF_RE.finditer(data[:200000]):
        for k, v in person_values(m.group(1).decode("latin-1")):
            out.append((k, v, where))
    for rx in (XML_FIELD_RE, XML_ATTR_RE, FREECAD_PROP_RE):
        for m in rx.finditer(data):
            field = m.group(1).decode("latin-1").lower()
            v = m.group(2).decode("utf-8", "replace")
            if field in ("company", "publisher", "owner", "rights", "copyright", "licenseterms"):
                out += [(k, x, where) for k, x in org_values(v)]
            elif field in ("application", "title"):
                continue
            else:
                out += [(k, x, where) for k, x in person_values(v)]
    return out


def harvest_step(data: bytes, where):
    out = []
    h = data[:20000]
    m = re.search(rb"FILE_NAME\s*\((.*?)\)\s*;", h, re.S)
    if not m:
        return out
    body = re.sub(rb"/\*.*?\*/", b"", m.group(1), flags=re.S).decode("latin-1")
    strs = re.findall(r"'((?:[^']|'')*)'", body)
    # name, time_stamp, author(list), organization(list), preprocessor, originating, authorisation
    groups = re.findall(r"\(([^()]*)\)", body)
    if len(groups) >= 2:
        for v in re.findall(r"'((?:[^']|'')*)'", groups[0]):
            out += [(k, x, where) for k, x in person_values(v)]
        for v in re.findall(r"'((?:[^']|'')*)'", groups[1]):
            out += [(k, x, where) for k, x in org_values(v)]
    if strs:
        auth = strs[-1]
        out += [(k, x, where) for k, x in person_values(auth)]
    return out


def harvest_iges(data: bytes, where):
    out = []
    g = b"".join(line[:72] for line in data[:40000].split(b"\n") if len(line) >= 73 and line[72:73] == b"G")
    try:
        txt = g.decode("latin-1")
        pd, rd = ",", ";"
        if txt.startswith("1H"):
            pd = txt[2]
        fields = []
        i = 0
        cur = ""
        while i < len(txt):
            m = re.match(r"(\d+)H", txt[i:])
            if m and (cur == "" or cur.strip() == ""):
                n = int(m.group(1))
                s = i + len(m.group(0))
                cur = txt[s:s + n]
                i = s + n
                continue
            c = txt[i]
            if c == pd or c == rd:
                fields.append(cur)
                cur = ""
                if c == rd:
                    break
            else:
                cur += c
            i += 1
        # 1-based global parameters 21 = author, 22 = organization
        if len(fields) > 21:
            out += [(k, x, where) for k, x in person_values(fields[20])]
            out += [(k, x, where) for k, x in org_values(fields[21])]
    except Exception:
        pass
    return out


TITLE_LABEL_RE = re.compile(
    r"\b(?:DRAWN|DESIGNED|DESIGNER|DRAFTER|DRAFTED|CHECKED|CHECKER|APPROVED|APPROVAL|APPR|ENGINEER|ENG|"
    r"MFG|QA|CHK|DWN|AUTHOR|Drawn|Designed|Designer|Checked|Approved|Author|Engineer|Gezeichnet|Geprüft|"
    r"Bearbeitet|Dessiné|Vérifié|Dibujado|Revisado|Disegnato|Controllato|Ersteller)(?![A-Za-z])"
    r"(?:\s+(?:BY|By|by|von|par|por))?\s*[:.]?[ \t]*\n?[ \t]*\n?[ \t]*"
    r"([A-Z][A-Za-z.'\-]{1,24}(?:[ \t][A-Z][A-Za-z.'\-]{1,24})?)"
    r"(?=[ \t]*(?:\n|$|\d{1,2}[ ./\-]|\d{4}|[A-Z]{3}\b))"
)
NOT_NAME = {"DATE", "SCALE", "SIZE", "SHEET", "TITLE", "REV", "DWG", "NO", "NAME", "SIGNATURE", "MATERIAL",
            "FINISH", "WEIGHT", "QTY", "DRAWN", "CHECKED", "APPROVED", "DESIGNER", "DRAFTER", "CHECKER",
            "APPROVAL", "ENG", "MFG", "QA", "TOLERANCES", "UNLESS", "DIMENSIONS", "THIRD", "FIRST", "ANGLE",
            "PROJECTION", "SURFACE", "MM", "INCH", "INCHES", "NOTES", "DO", "BY", "THE", "AND", "PART",
            "NUMBER", "DESCRIPTION", "ITEM", "SHEETS", "OF", "DEPT", "TECHNICAL", "REFERENCE", "CREATED",
            "DOCUMENT", "TYPE", "STATUS", "APPV", "ISSUE", "CHANGE", "ECN", "ECO", "SIGN", "DATUM", "NAME:",
            "APPR", "OVED", "IN", "FOR", "TO", "AS", "SEE", "ALL", "PER", "WITH", "ON", "AT"}


def harvest_title_block(text: str, where):
    out = []
    for m in TITLE_LABEL_RE.finditer(text):
        v = m.group(1).strip()
        toks = v.replace(".", " ").split()
        if not toks or any(t.upper().strip(".") in NOT_NAME for t in toks):
            continue
        if len(v) < 3 or is_placeholder(v) or SOFTWARE_RE.search(v):
            continue
        out.append(("person", v, where))
    return out


def pdf_page_text(data: bytes, maxpages=6) -> str:
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        parts = []
        for i in range(min(len(doc), maxpages)):
            tp = doc[i].get_textpage()
            parts.append(tp.get_text_range())
        doc.close()
        return "\n".join(parts)
    except Exception:
        return ""


def harvest_pdf(data: bytes, where):
    out = []
    try:
        txt = pdf_page_text(data)
        if txt:
            out += harvest_title_block(txt.replace("\r", "\n"), where)
            out += harvest_text(txt.encode("utf-8", "replace"), where)
    except Exception:
        pass
    try:
        import pikepdf
        with pikepdf.open(io.BytesIO(data)) as pdf:
            di = pdf.docinfo
            for key in ("/Author", "/Company", "/Manager", "/LastModifiedBy", "/SourceModified"):
                if key in di:
                    v = str(di[key])
                    if key == "/Company":
                        out += [(k, x, where) for k, x in org_values(v)]
                    else:
                        out += [(k, x, where) for k, x in person_values(v)]
            for key in ("/Creator", "/Producer", "/Title", "/Subject", "/Keywords"):
                if key in di:
                    out += harvest_text(str(di[key]).encode("utf-8", "replace"), where)
            if "/Metadata" in pdf.Root:
                try:
                    out += harvest_text(pdf.Root.Metadata.read_bytes(), where)
                except Exception:
                    pass
            for name, att in pdf.attachments.items():
                try:
                    out += harvest(where + "#" + name, att.get_file().read_bytes())
                except Exception:
                    pass
    except Exception:
        pass
    return out


def harvest_ole(data: bytes, where):
    out = []
    try:
        import olefile
        from .oleprops import parse
        o = olefile.OleFileIO(io.BytesIO(data))
        for s in o.listdir():
            if not s[-1].startswith("\x05"):
                continue
            for d in parse(o.openstream(s).read()):
                k = formats.ole_field_kind(d)
                if k == "person":
                    out += [(kk, x, where) for kk, x in person_values(d["value"])]
                elif k == "org":
                    out += [(kk, x, where) for kk, x in org_values(d["value"])]
                elif k == "account":
                    v = clean(d["value"])
                    if not is_placeholder(v):
                        out.append(("account", v, where))
                elif d["value"]:
                    out += harvest_text(d["value"].encode("utf-8", "replace"), where)
        o.close()
    except Exception:
        pass
    return out


def harvest_zip(data: bytes, where):
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        for info in z.infolist():
            if info.is_dir() or info.file_size > 400 << 20:
                continue
            out += harvest(where + "!" + info.filename, z.read(info))
    except Exception:
        pass
    return out


def harvest(where: str, data: bytes):
    kind = formats.sniff(where, data)
    out = []
    try:
        if kind == "pdf":
            out += harvest_pdf(data, where)
        elif kind == "ole":
            out += harvest_ole(data, where)
        elif kind == "zip":
            out += harvest_zip(data, where)
        elif kind == "sw3d":
            for name, raw in formats.sw3d_streams(data):
                if name.startswith(b"docProps") or name.endswith(b".xml"):
                    out += harvest_text(raw, where)
                else:
                    out += harvest_text(raw, where)
        elif kind in ("gz", "tar", "7z", "Z", "bz2", "xz"):
            for name, raw in formats.archive_members(kind, data):
                out += harvest(where + "!" + name, raw)
        if kind == "step":
            out += harvest_step(data, where)
        if kind == "iges":
            out += harvest_iges(data, where)
        if kind not in ("zip", "pdf", "sw3d"):
            out += harvest_text(data, where)
    except Exception:
        pass
    return out
