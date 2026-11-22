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
    "微软用户", "微軟使用者", "用户", "使用者", "管理员", "ユーザー", "사용자",
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
XML_PROP_RE = re.compile(rb"<property\b[^>]*\bname=\"([^\"]{1,80})\"[^>]*>\s*<vt:(?:lpwstr|lpstr|bstr)>([^<]{1,160})</vt:", re.I)
FREECAD_PROP_RE = re.compile(rb"<Property name=\"(CreatedBy|LastModifiedBy|Company)\"[^>]*>\s*<String value=\"([^\"]{1,120})\"")

NS_DOMAINS = re.compile(
    r"(w3\.org|adobe\.com|purl\.org|openxmlformats\.org|microsoft\.com|schemas\.|xmlsoap|iptc\.org|"
    r"inkscape\.org|sodipodi|creativecommons\.org|apache\.org|gnu\.org|ietf\.org|iso\.org|example\.|"
    r"google\.com|googleapis\.com|gstatic|pki\.goog|digicert|verisign|thawte|globalsign|ns\.|xmlns|"
    r"solidworks\.com|autodesk\.com|freecad|opencascade|spatial\.com|steptools\.com|ptc\.com|siemens\.com|"
    r"dassault|3ds\.com|onshape\.com|cairographics|libreoffice|openoffice|github\.com|gitlab\.com|"
    r"wikipedia\.org|youtube\.com|python\.org|mozilla|apple\.com|java\.com|oracle\.com|sun\.com|"
    r"sourceforge\.net|amazon\.com|cloudfront\.net|dropbox|wordpress\.com|wp\.com|jquery|bootstrap|"
    r"fonts\.|cdn\.|localhost|iec\.ch|color\.org|aiim\.org|npes\.org|dublincore\.org|schema\.org|spdx\.org|"
    r"json-schema\.org|xfa\.org|pdfa\.org|iso\.ch|ansi\.org|din\.de|w3c|unicode\.org|ecma)",
    re.I,
)


def clean(v: str) -> str:
    v = v.replace("\x00", "").strip().strip("'\"()[]{};,")
    return re.sub(r"\s+", " ", v)


VENDORS = re.compile(
    r"\b(apple|hewlett|packard|\bhp\b|microsoft|adobe|monotype|linotype|bitstream|urw|google|ibm|"
    r"sun microsystems|international color consortium|kodak|agfa|canon|epson|xerox|corel|autodesk|dassault|"
    r"siemens|ptc|parametric technology|ricoh|lexmark|brother|fujifilm|eastman|x-rite|gretag|unicode|"
    r"free software foundation|gnu|apache|mozilla|ghostscript|artifex|matra|datavision|open cascade|"
    r"elsevier|springer|wiley|ieee|creative commons|tracepart|cadenas|3d ?content ?central|grabcad)\b",
    re.I,
)


DATE_LIKE = re.compile(
    r"\d{1,2}:\d{2}|\b(19|20)\d{2}\b|\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|"
    r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|"
    r"oct|october|nov|november|dec|december)\b|"
    r"\b(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|"
    r"januar|februar|märz|juni|juli|oktober|dezember|enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre|gennaio|febbraio|aprile|maggio|giugno|luglio|settembre|"
    r"ottobre|dicembre|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|montag|dienstag|mittwoch|"
    r"donnerstag|freitag|samstag|sonntag|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
    r"\b(datum|plane|axis|origin|sketch|default|standard|metric|inch)\b",
    re.I,
)


def is_placeholder(v: str) -> bool:
    lo = v.lower().strip()
    if VENDORS.search(lo) or DATE_LIKE.search(lo) or "unknown" in lo:
        return True
    if lo in PLACEHOLDER or sum(1 for c in lo if c.isalpha()) < 2:
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


PHONE_RE = re.compile(
    rb"(?i)\b(?:tel(?:ephone)?|phone|fax|mobile|mob|cell|t\xc3\xa9l\xc3\xa9phone|telefon|tel\xc3\xa9fono|handy)\.?\s*"
    rb"(?:\([a-z]+\))?\s*[:.]?\s*(\+?\(?\d[\d\s().\-/]{6,20}\d)")


ORG_SUFFIX_RE = re.compile(
    rb"\b((?:(?:[A-Z][A-Za-z0-9&'\-]+|&|of|and)[ ]){0,5}[A-Z][A-Za-z0-9&'\-]+),? (Inc\.?|Incorporated|GmbH|mbH|Ltd\.?|Limited|LLC|"
    rb"Corp\.?|Corporation|Co\.,? ?Ltd\.?|AG|S\.A\.|S\.p\.A\.|SpA|S\.r\.l\.|Srl|B\.V\.|N\.V\.|Oy|A/S|"
    rb"Pty\.? Ltd\.?|PLC|KG|SAS|SARL)(?![A-Za-z])")
INST_OF_RE = re.compile(
    rb"\b((?:University|Universit\xc3\xa4t|Universidad|Universit\xc3\xa9|Institute|Instituto|Istituto|Hochschule|"
    rb"Politecnico)(?: of| de| f\xc3\xbcr| di)?(?: [A-Z][A-Za-z\-]+){1,3})")
INST_X_RE = re.compile(rb"\b((?:[A-Z][A-Za-z\-]+ ){1,3}(?:University|Institute of Technology|National Laboratory|Laboratory))\b")
INST_RE = re.compile(
    rb"\b((?:University|Universit\xc3\xa4t|Universidad|Universit\xc3\xa9|Institute|Instituto|Istituto|Hochschule|"
    rb"Politecnico)(?: of| de| f\xc3\xbcr| di)?(?: [A-Z][A-Za-z\-]+){1,3})|"
    rb"\b((?:[A-Z][A-Za-z\-]+ ){1,3}(?:University|Institute of Technology|National Laboratory|Laboratory))\b")


def _all_common(v: str) -> bool:
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        return False
    toks = [t for t in re.split(r"[\s,.&'\-]+", v) if t]
    stop = {"inc", "ltd", "llc", "corp", "corporation", "gmbh", "co", "company", "limited", "ag", "sa", "university",
            "of", "institute", "technology", "laboratory", "the", "de", "di", "für", "national"}
    core = [t for t in toks if t.lower() not in stop]
    return not core or all(max(zipf_frequency(t.lower(), l) for l in ("en", "de", "fr")) >= 3.3 for t in core)


def harvest_orgs(data: bytes, where: str):
    out = []
    if b"Inc" in data or b"Ltd" in data or b"GmbH" in data or b"Corp" in data or b"LLC" in data or b"AG" in data \
            or b"S.A" in data or b"B.V" in data or b"SpA" in data:
        for m in ORG_SUFFIX_RE.finditer(data):
            name = m.group(1).decode("utf-8", "replace")
            toks = name.split()
            while toks and toks[0].lower() in ("copyright", "by", "from", "of", "and", "the", "this", "our", "your",
                                               "a", "an", "all", "any", "each", "such", "other"):
                toks = toks[1:]
            while toks and toks[-1].lower() in ("of", "and", "&"):
                toks = toks[:-1]
            if not toks or (len(toks) == 1 and _all_common(toks[0]) and not re.search(r"[a-z]-?[A-Z]", toks[0])):
                continue
            name = " ".join(toks)
            v = name + m.group(0).decode("utf-8", "replace")[len(m.group(1).decode("utf-8", "replace")):]
            out += [(k, x, where) for k, x in org_values(v.strip())]
    if b"Univers" in data or b"Institut" in data or b"Laborator" in data or b"Istituto" in data or b"Hochschule" in data:
        found = []
        for rx in (INST_OF_RE, INST_X_RE):
            for m in rx.finditer(data):
                found.append(m.group(1).decode("utf-8", "replace").strip())
        for v in found:
            toks = v.split()
            while toks and toks[0].lower() in ("the", "this", "our", "your", "a", "an", "and", "at", "from"):
                toks = toks[1:]
            if len(toks) >= 2:
                out += [(k, x, where) for k, x in org_values(" ".join(toks))]
    return out


_PHONE_KW = re.compile(rb"Tel|TEL|tel|Phone|PHONE|phone|Fax|FAX|fax|Mob|MOB|mob|Cell|CELL|cell|T\xc3\xa9l|Handy|HANDY")


def _phone_matches(data: bytes):
    """PHONE_RE, but only tried where a label word occurs (a plain
    case-insensitive scan of every byte is ~40x slower)."""
    last = -1
    for k in _PHONE_KW.finditer(data):
        s = k.start()
        if s < last:
            continue
        m = PHONE_RE.match(data, s)
        if m is None and s > 0:
            continue
        if m:
            last = m.end()
            yield m


def harvest_text(data: bytes, where: str):
    out = []
    for m in _phone_matches(data):
        num = m.group(1).decode("latin-1").strip()
        if sum(c.isdigit() for c in num) >= 7:
            out.append(("phone", num, where))
    for m in EMAIL_RE.finditer(data):
        e = m.group().decode("latin-1").lstrip("-._")
        local, dom = e.split("@", 1)
        if not (dom == dom.lower() or dom.split(".")[-1] == dom.split(".")[-1].upper() == dom.split(".")[-1] and dom[0].isalpha() and len(dom.split(".")[-2]) >= 3):
            continue
        if len(local) < 2:
            continue
        # short random-looking local parts only count with an all-lower-case domain
        if re.fullmatch(r"[A-Za-z0-9]{1,6}", local) and not re.fullmatch(r"[a-z]+|[A-Z][a-z]+|[A-Z][a-z]+[A-Z]", local) \
                and dom != dom.lower():
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
    if b"<property" in data or b"<Property" in data:
        for m in XML_PROP_RE.finditer(data):
            pname = m.group(1).decode("utf-8", "replace")
            val = m.group(2).decode("utf-8", "replace")
            k = formats.ole_field_kind({"fmtid": "", "pid": -1, "name": pname.replace("SW-", "")})
            if k == "person":
                out += [(kk, x, where) for kk, x in person_values(val)]
            elif k == "org":
                out += [(kk, x, where) for kk, x in org_values(val)]
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
    r"Bearbeitet|Dessiné|Vérifié|Dibujado|Revisado|Disegnato|Controllato|Ersteller|Contact Name|Contact Person|"
    r"Contact|Prepared|Submitted|Ansprechpartner|Kontakt)(?![A-Za-z])"
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


def pdf_page_text(data: bytes, maxpages=400) -> str:
    """Text of the whole document (pdftotext, as text extractors see it);
    pypdfium2 as a fallback."""
    try:
        import subprocess
        r = subprocess.run(["pdftotext", "-q", "-l", str(maxpages), "-", "-"], input=data,
                           capture_output=True, timeout=120)
        if r.stdout:
            return r.stdout.decode("utf-8", "replace")
    except Exception:
        pass
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
            out += harvest_orgs(txt.encode("utf-8", "replace"), where)
            from .pii_detect import candidates
            for k, v in candidates(txt):
                if k == "person" and not is_placeholder(v):
                    out.append(("person", v.strip(), where))
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
        if kind in ("text", "step", "iges") and len(data) < 50_000_000:
            out += harvest_orgs(data, where)
    except Exception:
        pass
    return out
