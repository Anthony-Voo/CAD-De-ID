"""Per-file de-identification, routed by content (magic bytes) then extension.

Every handler keeps the file usable:
  * containers (zip family: .zip .fcstd .3mf .docx ..., gzip, 7z) are opened,
    each member is processed recursively (names and content) and they are
    re-packed with the same compression, so no unprocessed original remains;
  * PNG text chunks are scrubbed and their CRCs recomputed;
  * SolidWorks 2015+ files (compressed, checksummed sections) are kept
    byte-identical in this version (renamed only) and listed in README.txt;
  * everything else gets the same-length scrub (offsets never move), plus
    field-aware rules for STEP headers and PDF/XMP authors.
"""
import gzip
import io
import os
import re
import struct
import tempfile
import zipfile
import zlib

from .pseudo import pseudonym

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP = (b"PK\x03\x04", b"PK\x05\x06")
SW_EXT = (".sldprt", ".sldasm", ".slddrw", ".slddrt", ".sldlfp")
PNG = b"\x89PNG\r\n\x1a\n"


class Stats:
    def __init__(self):
        self.c = {}

    def add(self, k, n=1):
        self.c[k] = self.c.get(k, 0) + n


# ------------------------------------------------------------------ field rules
_STEP_FN = re.compile(rb"FILE_NAME\s*\((.*?)\)\s*;", re.S)
_STEP_STR = re.compile(rb"'((?:[^']|'')*)'")


def _same_len(text: bytes, cls: str) -> bytes:
    s = text.decode("latin-1")
    if not s.strip() or s.strip("$ ") == "" or s.strip().lower() in ("unknown", "none", "n/a", "author", "organization", "user"):
        return text
    r = pseudonym(s, cls).encode("latin-1", "replace")
    return r if len(r) == len(text) else text


def step_header(data: bytes) -> bytes:
    """FILE_NAME(name, time, (authors), (orgs), preprocessor, originating_system, authorization)."""
    head_end = data.find(b"ENDSEC;")
    if head_end < 0 or head_end > 200_000:
        return data
    head = data[:head_end]
    m = _STEP_FN.search(head)
    if not m:
        return data
    body = m.group(1)
    # split top-level arguments
    args, depth, cur, q = [], 0, bytearray(), False
    for ch in body:
        c = bytes([ch])
        if c == b"'":
            q = not q
        if not q and c == b"(":
            depth += 1
        if not q and c == b")":
            depth -= 1
        if not q and depth == 0 and c == b",":
            args.append(bytes(cur))
            cur = bytearray()
            continue
        cur += c
    args.append(bytes(cur))
    if len(args) < 7:
        return data
    cls_for = {2: "person", 3: "org", 6: "person"}
    new_args = []
    for i, a in enumerate(args):
        if i in cls_for:
            a = _STEP_STR.sub(lambda mm: b"'" + _same_len(mm.group(1), cls_for[i]) + b"'", a)
        new_args.append(a)
    new_body = b",".join(new_args)
    if len(new_body) != len(body):
        return data
    s = m.start(1)
    return data[:s] + new_body + data[s + len(body):]


_PDF_AUTHOR = re.compile(rb"/(Author|Creator(?!Tool))\s*\(((?:\\.|[^\\)])*)\)")
_XMP_CREATOR = re.compile(rb"(<dc:creator>\s*<rdf:Seq>\s*<rdf:li[^>]*>)([^<]{1,200})(</rdf:li>)")
_XMP_AUTHOR = re.compile(rb"(<pdf:Author>)([^<]{1,200})(</pdf:Author>)")
_PDF_SOFTWARE = re.compile(rb"(?i)(word|excel|powerpoint|acrobat|pdf|writer|solidworks|autocad|inventor|creo|latex|tex|cairo|ghostscript|draw|calc|impress|libreoffice|openoffice|microsoft|adobe|freecad|kicad|fusion|onshape|chrome|skia|quartz|printer)")


def pdf_fields(data: bytes) -> bytes:
    def info(m):
        key, val = m.group(1), m.group(2)
        if key == b"Creator" and _PDF_SOFTWARE.search(val):
            return m.group()  # application name, not a person
        if val.startswith(b"\xfe\xff") or b"\\" in val:
            return m.group()  # UTF-16 / escaped: left to the generic scrub
        return m.group()[: m.start(2) - m.start()] + _same_len(val, "person") + b")"
    data = _PDF_AUTHOR.sub(info, data)
    data = _XMP_CREATOR.sub(lambda m: m.group(1) + _same_len(m.group(2), "person") + m.group(3), data)
    data = _XMP_AUTHOR.sub(lambda m: m.group(1) + _same_len(m.group(2), "person") + m.group(3), data)
    return data


def png(data: bytes, scrub) -> bytes:
    out, i = bytearray(data[:8]), 8
    while i + 8 <= len(data):
        n, typ = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8: i + 8 + n]
        if typ in (b"tEXt", b"iTXt", b"zTXt", b"eXIf"):
            body, _ = scrub(body)
        out += struct.pack(">I4s", len(body), typ) + body + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF)
        i += 12 + n
        if typ == b"IEND":
            break
    out += data[i:]
    return bytes(out)


# ------------------------------------------------------------------ dispatcher
class Processor:
    def __init__(self, scrubber, rename_name, stats: Stats):
        self.sc = scrubber
        self.rename = rename_name  # str -> str for a single path component
        self.stats = stats

    def file(self, name: str, data: bytes, depth: int = 0) -> bytes:
        low = name.lower()
        try:
            if data[:4] in ZIP and depth < 6:
                self.stats.add("zip")
                return self.zipfile(data, depth)
            if data[:2] == b"\x1f\x8b" and depth < 6:
                self.stats.add("gzip")
                inner = gzip.decompress(data)
                inner_name = low[:-3] if low.endswith(".gz") else low
                out = io.BytesIO()
                with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as g:
                    g.write(self.file(inner_name, inner, depth + 1))
                return out.getvalue()
            if data[:6] == b"7z\xbc\xaf'\x1c" and depth < 6:
                self.stats.add("7z")
                return self.sevenzip(data, depth)
            if data[:8] == PNG:
                self.stats.add("png")
                return png(data, self.sc.scrub)
            if low.endswith(SW_EXT) and data[:8] != OLE:
                self.stats.add("sw2015_kept")
                return data
        except Exception as e:  # damaged/unsupported container: never drop the file
            self.stats.add(f"container_error:{type(e).__name__}")
            # fall through to the same-length scrub: it rewrites plain-text member names in the
            # archive directory (CRCs cover data, not names); compressed data cannot match.
        if low.endswith((".step", ".stp", ".p21")):
            data = step_header(data)
        if data[:5] == b"%PDF-" or low.endswith(".pdf"):
            data = pdf_fields(data)
        new, n = self.sc.scrub(data)
        self.stats.add("scrubbed_files")
        self.stats.add("replacements", n)
        return new

    def zipfile(self, data: bytes, depth: int) -> bytes:
        src = zipfile.ZipFile(io.BytesIO(data))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dst:
            for info in src.infolist():
                body = src.read(info)
                parts = info.filename.split("/")
                newname = "/".join(self.rename(p) if p else p for p in parts)
                if not info.is_dir():
                    body = self.file(parts[-1], body, depth + 1)
                zi = zipfile.ZipInfo(newname, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zi.create_system = info.create_system
                zi.comment = self.sc.scrub(info.comment)[0] if info.comment else b""
                dst.writestr(zi, body)
            if src.comment:
                dst.comment = self.sc.scrub(src.comment)[0]
        return out.getvalue()

    def sevenzip(self, data: bytes, depth: int) -> bytes:
        import py7zr
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "in"), os.path.join(td, "out")
            with py7zr.SevenZipFile(io.BytesIO(data)) as z:
                z.extractall(a)
            for root, dirs, files in os.walk(a):
                rel = os.path.relpath(root, a)
                newrel = "/".join(self.rename(p) for p in rel.split(os.sep)) if rel != "." else ""
                os.makedirs(os.path.join(b, newrel), exist_ok=True)
                for f in files:
                    body = open(os.path.join(root, f), "rb").read()
                    open(os.path.join(b, newrel, self.rename(f)), "wb").write(self.file(f, body, depth + 1))
            buf = io.BytesIO()
            with py7zr.SevenZipFile(buf, "w") as z:
                z.writeall(b, arcname="")
            return buf.getvalue()
