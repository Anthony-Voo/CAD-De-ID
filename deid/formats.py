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
    if name and re.search(r"date|datum|fecha|\bdata\b|time|zeit|heure|uhrzeit|jour|tag\b|format|size|scale|"
                          r"revision|\brev\b|version|number|nummer|numero|qty|quantity|mass|masse|weight|material|"
                          r"finish|unit|density|cost|price|status|description|beschreibung|titel|title|"
                          r"keywords|comments|subject", name):
        return None
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
    if name and re.search(r"author|designer|drawn|engineer|checked|approved|creat(?!ed date|ion date|ed$)|owner|dessin|dessiné|"
                          r"saved by|last saved|letzten speicherung von|zuletzt gespeichert von|gespeichert von|"
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


def _deflate_tails():
    """Byte strings that end a byte-aligned (sync-flushed) deflate stream:
    k empty non-final fixed-Huffman blocks followed by an empty final block
    (fixed or stored).  Lengths cover every remainder mod 5."""
    def build(k, final_stored):
        bits = []
        for _ in range(k):
            bits += [0, 1, 0] + [0] * 7          # BFINAL=0, BTYPE=01, EOB (7 zero bits)
        if final_stored:
            bits += [1, 0, 0]                    # BFINAL=1, BTYPE=00
            while len(bits) % 8:
                bits.append(0)
            out = bytearray()
            for i in range(0, len(bits), 8):
                out.append(sum(b << j for j, b in enumerate(bits[i:i + 8])))
            return bytes(out) + b"\x00\x00\xff\xff"
        bits += [1, 1, 0] + [0] * 7              # BFINAL=1, BTYPE=01, EOB
        while len(bits) % 8:
            bits.append(0)
        out = bytearray()
        for i in range(0, len(bits), 8):
            out.append(sum(b << j for j, b in enumerate(bits[i:i + 8])))
        return bytes(out)
    tails = {}
    for k in range(0, 6):
        for st in (False, True):
            t = build(k, st)
            if zlib.decompressobj(-15).decompress(b"\x01\x00\x00\xff\xff"[:0] + b"\x00\x00\x00\xff\xff" + t) == b"":
                tails.setdefault(len(t), t)
    return tails


_TAILS = None


def _deflate_exact_one(raw: bytes, target: int):
    """One level-9 compression; exact size via empty padding blocks."""
    global _TAILS
    if _TAILS is None:
        _TAILS = _deflate_tails()
    co = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    body = co.compress(raw) + co.flush(zlib.Z_SYNC_FLUSH)
    rem = target - len(body)
    for tl in sorted(_TAILS):
        if tl <= rem and (rem - tl) % 5 == 0:
            return body + b"\x00\x00\x00\xff\xff" * ((rem - tl) // 5) + _TAILS[tl]
    return None


def _deflate_exact_fast(raw: bytes, target: int):
    """One compression per level: sync-flushed body + empty stored blocks as
    padding (5 bytes each) + an empty final block, either stored (5 bytes) or
    fixed-Huffman (2 bytes, 03 00).  Covers remainders 0 and 2 (mod 5)."""
    for level in (9, 8, 7, 6, 5, 4, 3, 2, 1):
        co = zlib.compressobj(level, zlib.DEFLATED, -15, 9)
        body = co.compress(raw) + co.flush(zlib.Z_SYNC_FLUSH)
        for fin in (b"\x01\x00\x00\xff\xff", b"\x03\x00"):
            rem = target - len(body) - len(fin)
            if rem >= 0 and rem % 5 == 0:
                return body + b"\x00\x00\x00\xff\xff" * (rem // 5) + fin
        if len(body) + 2 > target:
            break  # lower levels only get bigger
    return None


def deflate_exact(raw: bytes, target: int, allow_pad: bool = True):
    out = _deflate_exact_one(raw, target)
    if out is not None:
        return out
    out = _deflate_exact_fast(raw, target)
    if out is not None:
        return out
    # nothing compresses smaller than level 9: if even a sync-flushed level-9
    # body does not fit, only a few level-9 variants (no flush) can, so skip
    # the expensive searches below
    co = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    body9 = co.compress(raw) + co.flush(zlib.Z_SYNC_FLUSH)
    if len(body9) + 2 > target:
        best = None
        for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED):
            co = zlib.compressobj(9, zlib.DEFLATED, -15, 9, strategy)
            c = co.compress(raw) + co.flush()
            if len(c) == target:
                return c
            if len(c) < target and (best is None or len(c) > len(best)):
                best = c
        if best is not None and allow_pad:
            return best + b"\x00" * (target - len(best))
        return None
    out = _deflate_exact_blocks(raw, target)
    if out is not None:
        return out
    if not allow_pad:
        return None
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


# ------------------------------------------------- SolidWorks (<2015) __ZLB
ZLB_SIG_LEN = 16


def zlb_rewrite(raw: bytes, fn):
    """Rewrite a SolidWorks '..__ZLB' OLE stream in place.

    Layout: 16-byte signature, then blocks [u32 size][u32 csize][zlib data],
    ending with a zero block.  fn(bytes) -> same-length bytes is applied to
    each decompressed block, which is then recompressed to exactly csize
    (zlib header kept, Adler-32 recomputed).  Returns (new_raw, failed)."""
    if len(raw) < ZLB_SIG_LEN + 8:
        return raw, 0
    buf = bytearray(raw)
    p = ZLB_SIG_LEN
    failed = 0
    while p + 8 <= len(raw):
        u, c = struct.unpack_from("<II", raw, p)
        if u == 0 and c == 0:
            break
        z = raw[p + 8:p + 8 + c]
        if c < 6 or len(z) != c:
            break
        try:
            d = zlib.decompress(z)
        except zlib.error:
            break
        if len(d) != u:
            break
        nd = fn(d)
        if nd != d and len(nd) == len(d):
            body = deflate_exact(nd, c - 6, allow_pad=False)
            if body is not None and len(body) == c - 6:
                newz = z[:2] + body + struct.pack(">I", zlib.adler32(nd) & 0xFFFFFFFF)
                try:
                    good = zlib.decompress(newz) == nd
                except zlib.error:
                    good = False
                if good:
                    buf[p + 8:p + 8 + c] = newz
                else:
                    failed += 1
            else:
                failed += 1
        p += 8 + c
    return bytes(buf), failed


# ------------------------------------------------------ OLE unused areas
def ole_wipe_unused(data: bytes):
    """Zero the unallocated sectors of an OLE compound file and the slack
    after the end of every regular stream (bytes nobody reads, but which can
    keep old names from earlier saves).  Structure and sizes are unchanged.
    Returns (new_data, bytes_zeroed)."""
    import olefile
    try:
        o = olefile.OleFileIO(io.BytesIO(data))
    except Exception:
        return data, 0
    ss = o.sectorsize
    FREE = 0xFFFFFFFF
    buf = bytearray(data)
    zeroed = 0
    nsec = (len(data) - ss) // ss
    try:
        for i, nxt in enumerate(o.fat[:nsec]):
            if nxt == FREE:
                a = (i + 1) * ss
                if a + ss <= len(buf) and any(buf[a:a + ss]):
                    buf[a:a + ss] = bytes(ss)
                    zeroed += ss
        # slack at the end of regular (non-mini) streams
        for e in o.direntries:
            if e is None or e.entry_type not in (2, 5) or e.size < o.minisectorcutoff and e.entry_type == 2:
                continue
            size = e.size if e.entry_type == 2 else (len(o.ministream) if hasattr(o, "ministream") and o.ministream else 0)
            if e.entry_type == 5:
                continue
            sect, chain = e.isectStart, []
            while sect not in (0xFFFFFFFE, FREE) and sect < len(o.fat) and len(chain) <= nsec:
                chain.append(sect)
                sect = o.fat[sect]
            if not chain:
                continue
            used_in_last = size - (len(chain) - 1) * ss
            if 0 < used_in_last < ss:
                a = (chain[-1] + 1) * ss + used_in_last
                b = (chain[-1] + 1) * ss + ss
                if b <= len(buf) and any(buf[a:b]):
                    buf[a:b] = bytes(b - a)
                    zeroed += b - a
    except Exception:
        return data, 0
    finally:
        o.close()
    return bytes(buf), zeroed
