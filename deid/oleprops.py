"""Minimal MS-OLEPS property-set reader that reports string value locations.

olefile's parser mis-reads Inventor/Solid Edge property sets (they use
codepage 1200 and dictionaries), so we parse them ourselves and return, for
each string property, its id, name (from the dictionary if any), value and the
byte span of the value inside the stream.  The rewriter then replaces values
in place with same-length pseudonyms.
"""
from __future__ import annotations

import struct

VT_LPSTR = 0x1E
VT_LPWSTR = 0x1F

FMTID_SUMMARY = "F29F85E0-4FF9-1068-AB91-08002B27B3D9"
FMTID_DOCSUMMARY = "D5CDD502-2E9C-101B-9397-08002B2CF9AE"
FMTID_USER = "D5CDD505-2E9C-101B-9397-08002B2CF9AE"
FMTID_INV_DESIGN = "32853F0F-3444-11D1-9E93-0060B03C1CA6"

# Standard ids of identity-bearing properties.
SUMMARY_IDS = {4: "Author", 8: "LastAuthor", 2: "Title", 3: "Subject", 5: "Keywords", 6: "Comments"}
DOCSUMMARY_IDS = {14: "Manager", 15: "Company", 2: "Category"}


def _guid(b: bytes) -> str:
    a, b2, c = struct.unpack_from("<IHH", b, 0)
    d = b[8:16]
    return f"{a:08X}-{b2:04X}-{c:04X}-{d[:2].hex().upper()}-{d[2:].hex().upper()}"


def parse(stream: bytes):
    """Yield dicts: fmtid, pid, name, vt, value, start, end (value byte span)."""
    out = []
    if len(stream) < 48:
        return out
    try:
        bo, ver = struct.unpack_from("<HH", stream, 0)
        if bo != 0xFFFE:
            return out
        nsec = struct.unpack_from("<I", stream, 24)[0]
        for si in range(min(nsec, 4)):
            fmtid = _guid(stream[28 + si * 20: 44 + si * 20])
            off = struct.unpack_from("<I", stream, 44 + si * 20)[0]
            if off + 8 > len(stream):
                continue
            size, nprop = struct.unpack_from("<II", stream, off)
            pids = []
            for i in range(min(nprop, 4096)):
                pid, poff = struct.unpack_from("<II", stream, off + 8 + i * 8)
                pids.append((pid, poff))
            codepage = 1252
            for pid, poff in pids:
                if pid == 1:
                    vt = struct.unpack_from("<H", stream, off + poff)[0]
                    if vt == 2:
                        codepage = struct.unpack_from("<h", stream, off + poff + 4)[0] & 0xFFFF
            names = {}
            for pid, poff in pids:
                if pid == 0:  # dictionary
                    p = off + poff
                    n = struct.unpack_from("<I", stream, p)[0]
                    p += 4
                    for _ in range(min(n, 4096)):
                        dpid, ln = struct.unpack_from("<II", stream, p)
                        p += 8
                        if codepage == 1200:
                            raw = stream[p:p + ln * 2]
                            p += ln * 2
                            p += (-p) % 4 if False else 0
                            nm = raw.decode("utf-16-le", "replace").rstrip("\0")
                            if (ln * 2) % 4:
                                p += 2
                        else:
                            raw = stream[p:p + ln]
                            p += ln
                            nm = raw.decode("latin-1").rstrip("\0")
                        names[dpid] = nm
            for pid, poff in pids:
                if pid in (0, 1):
                    continue
                p = off + poff
                if p + 8 > len(stream):
                    continue
                vt = struct.unpack_from("<H", stream, p)[0]
                if vt == VT_LPSTR:
                    ln = struct.unpack_from("<I", stream, p + 4)[0]
                    s, e = p + 8, p + 8 + ln
                    if e > len(stream) or ln > 1 << 20:
                        continue
                    raw = stream[s:e]
                    if codepage == 1200:
                        # LPSTR stored as UTF-16 when the codepage is 1200
                        val = raw.decode("utf-16-le", "replace")
                        enc = "utf-16-le"
                    else:
                        enc = "cp1252" if codepage in (1252, 0) else ("utf-8" if codepage == 65001 else "latin-1")
                        val = raw.decode(enc, "replace")
                    z = val.find("\0")
                    if z >= 0:
                        e = s + len(val[:z].encode(enc, "replace"))
                        val = val[:z]
                    out.append(dict(fmtid=fmtid, pid=pid, name=names.get(pid), vt=vt, value=val, start=s, end=e, enc=enc))
                elif vt == VT_LPWSTR:
                    ln = struct.unpack_from("<I", stream, p + 4)[0]
                    s, e = p + 8, p + 8 + ln * 2
                    if e > len(stream) or ln > 1 << 20:
                        continue
                    val = stream[s:e].decode("utf-16-le", "replace")
                    z = val.find("\0")
                    if z >= 0:
                        e = s + z * 2
                        val = val[:z]
                    out.append(dict(fmtid=fmtid, pid=pid, name=names.get(pid), vt=vt, value=val, start=s, end=e, enc="utf-16-le"))
    except struct.error:
        pass
    return out
