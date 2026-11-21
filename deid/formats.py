"""Format detection and container helpers shared by discovery and rewriting."""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import struct
import subprocess
import tarfile
import zlib

from .oleprops import FMTID_DOCSUMMARY, FMTID_SUMMARY

TEXT_EXT = {
    "step", "stp", "p21", "iges", "igs", "dxf", "svg", "obj", "mtl", "wrl", "vrml", "x3d", "ps", "eps", "sat", "sab",
    "x_t", "xmt_txt", "unv", "brep", "brp", "txt", "md", "csv", "json", "xml", "html", "htm", "scad", "ini", "cfg",
    "yaml", "yml", "gcode", "nc", "ngc", "tap", "neu", "off", "ply", "dae", "gltf", "urdf", "sdf", "xacro", "py",
    "js", "c", "h", "cpp", "asm", "drw", "prt", "frm", "lst", "log", "kicad_pcb", "kicad_sch", "sch", "lib", "net",
    "dat", "bdf", "inp", "geo", "stl", "rtf", "tex", "bib", "sh", "bat", "m", "rst", "toml",
}


def ext_of(path: str) -> str:
    base = path.rsplit("/", 1)[-1].rsplit("!", 1)[-1]
    # Creo version suffixes: part.prt.12 -> prt
    m = re.search(r"\.([A-Za-z_0-9]+)\.\d+$", base)
    if m:
        return m.group(1).lower()
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def is_texty(data: bytes, n=4096) -> bool:
    d = data[:n]
    if not d:
        return False
    if d.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    bad = sum(1 for b in d if b < 9 or (13 < b < 32 and b != 27))
    return bad / len(d) < 0.01


SW3D_MARK = b"\x14\x00\x06\x00\x08\x00"


def sniff(path: str, data: bytes) -> str:
    h = data[:16]
    e = ext_of(path)
    if not data:
        return "empty"
    if h.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"
    if h.startswith(b"%PDF") or (b"%PDF-" in data[:1024] and e == "pdf"):
        return "pdf"
    if h.startswith(b"PK\x03\x04") or h.startswith(b"PK\x05\x06"):
        return "zip"
    if h.startswith(b"\x1f\x8b"):
        return "gz"
    if h.startswith(b"\x1f\x9d"):
        return "Z"
    if h.startswith(b"BZh"):
        return "bz2"
    if h.startswith(b"\xfd7zXZ"):
        return "xz"
    if h.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if h.startswith(b"Rar!"):
        return "rar"
    if len(data) > 262 and data[257:262] == b"ustar":
        return "tar"
    if e == "tar":
        return "tar"
    if h.startswith(b"\x89PNG"):
        return "png"
    if h.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if e in ("sldprt", "sldasm", "slddrw", "sldlfp", "slddrt", "prtdot", "asmdot", "drwdot", "sldblk") and SW3D_MARK in data[:64]:
        return "sw3d"
    if is_texty(data):
        if e in ("step", "stp", "p21") or data[:20].startswith(b"ISO-10303-21"):
            return "step"
        if e in ("iges", "igs"):
            return "iges"
        return "text"
    return "bin"


def ole_field_kind(d) -> str | None:
    """Classify an OLE string property as person / org / account / None."""
    f, pid, name = d["fmtid"], d["pid"], (d["name"] or "").lower()
    if f in (FMTID_SUMMARY, "3D38DE39-AA30-11D1-9C00-0060B03C1CA6") or f.startswith("3D38DE39"):
        if pid in (4, 8):
            return "person"
    if f == FMTID_DOCSUMMARY or f.startswith("8CF58000"):
        if pid == 14:
            return "person"
        if pid == 15:
            return "org"
    if f.startswith("32853F0F") and pid in (41, 42, 43):
        # Inventor design tracking: designer / engineer / authority
        return "person"
    if f.startswith("D861FB30") and pid == 16:
        return "account"
    if f.startswith("CC024FA2") and pid in (8, 20):
        return "person"
    if name and re.search(r"author|designer|drawn|engineer|checked|approved|creat|owner|dessin|dessiné|"
                          r"gezeichnet|bearbeitet|gepr|ersteller|autor|dissenyat|disenad|progett|"
                          r"verificat|manager|responsible|contact|e-?mail|phone|tel", name):
        return "person"
    if name and re.search(r"company|firm|organi|client|customer|kunde|societ|empresa|azienda|entreprise|"
                          r"university|institut|lab\b|department", name):
        return "org"
    return None


# ------------------------------------------------------------- SolidWorks 3D
def _nib(b: bytes) -> bytes:
    return bytes(((x << 4) & 0xF0) | (x >> 4) for x in b)


def sw3d_entries(data: bytes):
    """Yield (offset, header_len, name, comp_size, raw_size, crc, payload_offset)."""
    pos = 0
    n = len(data)
    while True:
        i = data.find(SW3D_MARK, pos)
        if i < 0 or i + 26 > n:
            return
        a, crc, c, u, ln = struct.unpack_from("<IIIII", data, i + 6)
        if 0 < ln < 512 and i + 26 + ln + c <= n and c < (1 << 31):
            name = _nib(data[i + 26:i + 26 + ln])
            po = i + 26 + ln
            if c and re.fullmatch(rb"[\x20-\x7e]+", name):
                try:
                    d = zlib.decompressobj(-15)
                    raw = d.decompress(data[po:po + c], u + 16)
                    if len(raw) == u and zlib.crc32(raw) == crc:
                        yield (i, 26 + ln, name, c, u, crc, po)
                        pos = po + c
                        continue
                except zlib.error:
                    pass
        pos = i + 1


def sw3d_streams(data: bytes):
    for i, hl, name, c, u, crc, po in sw3d_entries(data):
        yield name, zlib.decompress(data[po:po + c], -15)


def deflate_exact(raw: bytes, target: int):
    out = _deflate_exact_blocks(raw, target)
    if out is not None:
        return out
    # search encoder parameters for an exact size, then fall back to a
    # shorter complete stream padded with zeros after its final block
    # (inflate stops at the final block and ignores trailing bytes)
    best = None
    budget = 120 if len(raw) < 1 << 16 else 4
    tries = 0
    for level in (9, 8, 7, 6, 5, 4, 3, 2, 1):
        for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED):
            for mem in (9, 8, 7, 6, 5, 4):
                for wbits in (-15, -14, -13, -12, -11, -10, -9):
                    tries += 1
                    if tries > budget:
                        break
                    co = zlib.compressobj(level, zlib.DEFLATED, wbits, mem, strategy)
                    c = co.compress(raw) + co.flush()
                    if len(c) == target:
                        return c
                    if len(c) < target and (best is None or len(c) > len(best)):
                        best = c
    if best is not None:
        return best + b"\x00" * (target - len(best))
    return None


def _deflate_exact_blocks(raw: bytes, target: int):
    """Raw-deflate `raw` into exactly `target` bytes (or None).

    Compress all but the last r bytes with a sync flush (non-final blocks),
    pad with empty stored blocks (5 bytes each), then a final stored block
    holding the last r bytes (5 + r bytes)."""
    big = len(raw) > 1 << 16
    levels = (9, 6) if big else (9, 6, 8, 7, 5, 4, 3, 2, 1)
    strategies = (zlib.Z_DEFAULT_STRATEGY,) if big else (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED)
    for level in levels:
        for strategy in strategies:
            for r in range(0, min(len(raw), 6 if big else 40) + 1):
                key = (level, strategy, r)
                head = raw[:len(raw) - r]
                co = zlib.compressobj(level, zlib.DEFLATED, -15, 9, strategy)
                body = co.compress(head) + co.flush(zlib.Z_SYNC_FLUSH)
                tail = raw[len(raw) - r:]
                base = len(body) + 5 + len(tail)
                if base > target:
                    break  # larger r only adds bytes
                if (target - base) % 5 == 0:
                    pad = b"\x00\x00\x00\xff\xff" * ((target - base) // 5)
                    fin = b"\x01" + struct.pack("<HH", len(tail), len(tail) ^ 0xFFFF) + tail
                    out = body + pad + fin
                    if len(out) == target:
                        return out
    return None


# ------------------------------------------------------------------ archives
def archive_members(kind: str, data: bytes):
    """Yield (name, bytes) for single-stream and tar-like archives (read only)."""
    if kind == "gz":
        raw = gzip.decompress(data)
        if len(raw) > 262 and raw[257:262] == b"ustar":
            yield from archive_members("tar", raw)
        else:
            yield ("(gunzip)", raw)
    elif kind == "bz2":
        yield ("(bunzip2)", bz2.decompress(data))
    elif kind == "xz":
        yield ("(unxz)", lzma.decompress(data))
    elif kind == "Z":
        p = subprocess.run(["gzip", "-dc"], input=data, capture_output=True)
        if p.stdout:
            yield ("(uncompress)", p.stdout)
    elif kind == "tar":
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            for m in t.getmembers():
                if m.isfile():
                    f = t.extractfile(m)
                    if f:
                        yield (m.name, f.read())
    elif kind == "7z":
        yield from sevenzip_members(data)


def sevenzip_members(data: bytes):
    import os
    import tempfile
    import py7zr
    with tempfile.TemporaryDirectory(dir=os.environ.get("DEID_TMP")) as td:
        with py7zr.SevenZipFile(io.BytesIO(data)) as z:
            z.extractall(path=td)
        for root, _, files in os.walk(td):
            for f in sorted(files):
                p = os.path.join(root, f)
                with open(p, "rb") as fh:
                    yield (os.path.relpath(p, td).replace(os.sep, "/"), fh.read())
