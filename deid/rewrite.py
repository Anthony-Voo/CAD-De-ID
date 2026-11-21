"""Pass 2: rewrite file contents and paths.

transform(path, data, reg) -> (new_data, notes)
map_path(path, reg) -> new path

Everything is same-length except PDFs (re-saved by pikepdf) and repacked
archives.  Every handler falls back to the next safer level on error, and as
a last resort the original bytes are kept (and reported in README.txt).
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import struct
import subprocess
import tarfile
import zipfile
import zlib

from . import formats
from .harvest import harvest, clean, is_placeholder
from .pseudo import norm_key, pseudo_bytes, pseudo_text
from .registry import Registry
from .seeds import DOMAIN_KEEP, split_owner_repo

MAX_INMEM = 1_500_000_000


# =================================================================== paths
def map_domain(dom: str) -> str:
    out = []
    for lab in dom.split("."):
        if not lab or lab.lower() in DOMAIN_KEEP or lab.isdigit():
            out.append(lab)
        else:
            out.append(pseudo_text("id", norm_key(lab), lab))
    return ".".join(out)


def _map_component(comp: str, reg: Registry, is_file: bool) -> str:
    if is_file and "." in comp:
        # keep every extension untouched (".prt.3", ".tar.gz", ".hh.sat.stp" keeps the
        # format suffixes); only the stem is matched
        m = re.match(r"^(.*?)((?:\.[A-Za-z0-9_]{1,10})+)$", comp)
        stem, ext = (m.group(1), m.group(2)) if m and m.group(1) else (comp, "")
        return reg.replace_str(stem, "path") + ext
    return reg.replace_str(comp, "path")


def map_owner_repo(seg: str) -> str:
    parts = seg.split("__")
    out = []
    for i, p in enumerate(parts):
        if i >= 2 and re.fullmatch(r"[0-9a-f]{6,40}", p):
            out.append(p)
        elif p:
            out.append(pseudo_text("id", norm_key(p), p) if len(norm_key(p)) >= 2 else p)
        else:
            out.append(p)
    return "__".join(out)


def map_path(path: str, reg: Registry) -> str:
    a = path.split("/")
    out = list(a)
    if a[0] == "archives" and len(a) == 2:
        m = re.match(r"^(.*?)((?:\.[A-Za-z0-9]{1,5})+)$", a[1])
        stem, ext = m.groups() if m else (a[1], "")
        out[1] = map_owner_repo(stem) + ext
        return "/".join(out)
    start = 1
    if a[0] == "sources" and len(a) > 3:
        kind, seg = a[1], a[2]
        if kind == "github":
            out[2] = map_owner_repo(seg)
        elif kind == "gitlab":
            host, *rest = seg.split("__")
            out[2] = "__".join([host] + [pseudo_text("id", norm_key(x), x) if x and x != "project" else x for x in rest])
        elif kind == "web" or (kind == "catalog" and re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", seg)):
            out[2] = map_domain(seg)
        else:
            out[2] = _map_component(seg, reg, False)
        start = 3
    for i in range(start, len(a)):
        if a[0] == "sources" and i < 2:
            continue
        out[i] = _map_component(a[i], reg, i == len(a) - 1)
    return "/".join(out)


# ============================================================ local values
def local_registry(reg: Registry, where: str, data: bytes):
    """Registry extended with the identity values harvested from this file
    (field values are replaced even when they are short or common words)."""
    found = []
    try:
        found = harvest(where, data)
    except Exception:
        pass
    vals = {(k, clean(v)) for k, v, _ in found if v and not is_placeholder(clean(v))}
    if not vals:
        return reg, found
    from .registry import Overlay
    loc = Overlay(reg)
    for k, v in vals:
        if k == "email":
            local, dom = v.split("@", 1)
            loc.add("email", v, ctx=("path", "text", "bin"), substr=True)
            loc.add("account", local, ctx=("path", "text"))
            continue
        if k == "domain":
            continue  # domains are handled globally (labels may be generic)
        nk = norm_key(v)
        if len(nk) < 2:
            continue
        ctx = ("path", "text", "bin") if len(v) >= 5 else ("path", "text")
        if (k, nk) not in reg.ids or not reg.ids[(k, nk)].ctx:
            loc.add(k, v, ctx=ctx)
    return loc, found


# =================================================================== text
def rw_text(data: bytes, reg: Registry, ctx="text"):
    new, n = reg.replace(data, ctx)
    return new


def rw_bin(data: bytes, reg: Registry):
    new, n = reg.replace(data, "bin")
    return new


# ==================================================================== OLE
def rw_ole(data: bytes, reg: Registry, where: str, notes: list):
    import olefile
    from .oleprops import parse

    buf = bytearray(data)
    try:
        o = olefile.OleFileIO(io.BytesIO(bytes(buf)), write_mode=False)
    except Exception as e:
        notes.append(f"ole-open-failed:{e}")
        return rw_bin(data, reg)
    changes = []
    for s in o.listdir(streams=True, storages=False):
        try:
            raw = o.openstream(s).read()
        except Exception:
            continue
        name = s[-1]
        new = None
        if name.startswith("\x05"):
            nb = bytearray(raw)
            for d in parse(raw):
                kind = formats.ole_field_kind(d)
                span = raw[d["start"]:d["end"]]
                if kind and d["value"].strip() and not is_placeholder(clean(d["value"])):
                    nb[d["start"]:d["end"]] = pseudo_bytes("id", norm_key(d["value"]), span, d["enc"])
                else:
                    nb[d["start"]:d["end"]] = reg.replace(span, "text")[0]
            new = bytes(nb)
        elif len(raw) < 64 << 20:
            new = reg.replace(raw, "bin")[0]
        if new is not None and new != raw:
            changes.append((s, new))
    o.close()
    if not changes:
        return data
    import tempfile, os
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(data)
        tmp = tf.name
    try:
        ow = olefile.OleFileIO(tmp, write_mode=True)
        for s, new in changes:
            ow.write_stream("/".join(s), new)
        ow.close()
        out = open(tmp, "rb").read()
    except Exception as e:
        notes.append(f"ole-write-failed:{e}")
        out = data
    finally:
        os.unlink(tmp)
    # directory entry names may also hold identifiers (rare): leave them.
    return out


# ============================================================ SolidWorks 3D
def rw_sw3d(data: bytes, reg: Registry, notes: list):
    buf = bytearray(data)
    changed = failed = 0
    for i, hl, name, c, u, crc, po in list(formats.sw3d_entries(data)):
        raw = zlib.decompress(data[po:po + c], -15)
        if name.endswith(b".xml") or name.startswith(b"docProps") or name.startswith(b"swXml"):
            new = reg.replace(raw, "text")[0]
        else:
            new = reg.replace(raw, "bin")[0]
        if new == raw:
            continue
        comp = formats.deflate_exact(new, c)
        if comp is None:
            failed += 1
            continue
        buf[po:po + c] = comp
        struct.pack_into("<I", buf, i + 10, zlib.crc32(new))
        changed += 1
    if failed:
        notes.append(f"sw3d-streams-kept:{failed}")
    out = bytes(buf)
    # names in uncompressed parts of the container
    return reg.replace(out, "bin")[0] if changed == 0 else out


# ==================================================================== PDF
def rw_pdf(data: bytes, reg: Registry, where: str, notes: list):
    try:
        import pikepdf
    except ImportError:
        return rw_bin(data, reg)
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except Exception as e:
        notes.append(f"pdf-open-failed")
        return rw_bin(data, reg)
    try:
        # document info: identity fields get whole-value pseudonyms
        di = pdf.docinfo
        for key in list(di.keys()):
            try:
                v = str(di[key])
            except Exception:
                continue
            if key in ("/Author", "/Company", "/Manager", "/LastModifiedBy") and v.strip() and not is_placeholder(clean(v)):
                di[key] = pikepdf.String(pseudo_text("id", norm_key(v), v))
            elif key not in ("/CreationDate", "/ModDate", "/Trapped"):
                nv = reg.replace(v.encode("utf-8"), "text")[0].decode("utf-8", "replace")
                if nv != v:
                    di[key] = pikepdf.String(nv)
        # every object: strings and streams
        for obj in pdf.objects:
            _pdf_walk(obj, reg, pdf, notes, where)
        # visible text through the fonts (CID / Identity-H drawings)
        from .pdftext import rewrite_page_text
        for page in pdf.pages:
            try:
                rewrite_page_text(pdf, page, reg)
            except Exception as e:
                notes.append("pdf-text-error")
        if "/Metadata" in pdf.Root:
            try:
                m = pdf.Root.Metadata
                raw = m.read_bytes()
                new = rw_xmp(raw, reg)
                if new != raw:
                    m.write(new)
            except Exception:
                pass
        # embedded files
        try:
            for name in list(pdf.attachments.keys()):
                att = pdf.attachments[name]
                raw = att.get_file().read_bytes()
                nn = map_path(name, reg) if "/" in name else _map_component(name, reg, True)
                new, _ = transform(where + "#" + name, raw, reg)
                if new != raw or nn != name:
                    del pdf.attachments[name]
                    pdf.attachments[nn] = pikepdf.AttachedFileSpec(pdf, new, filename=nn)
        except Exception as e:
            notes.append("pdf-attachments-error")
        out = io.BytesIO()
        pdf.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.preserve)
        return out.getvalue()
    except Exception as e:
        notes.append(f"pdf-rewrite-failed:{str(e)[:80]}")
        return rw_bin(data, reg)
    finally:
        pdf.close()


def rw_xmp(raw: bytes, reg: Registry) -> bytes:
    def fld(m):
        v = m.group(2)
        try:
            s = v.decode("utf-8")
        except UnicodeDecodeError:
            return m.group(0)
        if s.strip() and not is_placeholder(clean(s)):
            return m.group(1) + pseudo_bytes("id", norm_key(s), v, "utf-8") + m.group(3)
        return m.group(0)
    rx = re.compile(rb"(<(?:dc:creator|pdf:Author|xmp:Author|photoshop:AuthorsPosition|xmpRights:Owner)[^>]*>\s*(?:<rdf:(?:Seq|Bag|Alt)>\s*<rdf:li[^>]*>)?)([^<]{1,200})(<)")
    raw = rx.sub(fld, raw)
    return reg.replace(raw, "text")[0]


def _pdf_walk(obj, reg, pdf, notes, where, depth=0):
    import pikepdf
    if depth > 6:
        return
    try:
        if isinstance(obj, pikepdf.Stream):
            d = obj
            typ = d.get("/Type")
            sub = d.get("/Subtype")
            if sub == "/Image" or typ == "/XRef" or typ == "/ObjStm" or "/FontFile" in str(d.keys()):
                return
            if typ == "/EmbeddedFile" or typ == "/Metadata":
                return  # handled separately
            if d.get("/Length1") is not None or sub in ("/Type1C", "/CIDFontType0C", "/OpenType"):
                return  # font programs
            try:
                raw = d.read_bytes()
            except Exception:
                return
            if len(raw) > 64 << 20:
                return
            new = _rw_content(raw, reg)
            if new != raw:
                d.write(new)
            for k in list(d.keys()):
                _pdf_walk(d[k], reg, pdf, notes, where, depth + 1)
        elif isinstance(obj, pikepdf.Dictionary):
            for k in list(obj.keys()):
                v = obj[k]
                if isinstance(v, pikepdf.String):
                    b = bytes(v)
                    nb = reg.replace(b, "text")[0]
                    if b.startswith(b"\xfe\xff"):
                        nb = _rw_utf16be(b, reg)
                    if nb != b:
                        obj[k] = pikepdf.String(nb)
                elif isinstance(v, (pikepdf.Array, pikepdf.Dictionary)) and not isinstance(v, pikepdf.Stream):
                    if not v.is_indirect:
                        _pdf_walk(v, reg, pdf, notes, where, depth + 1)
        elif isinstance(obj, pikepdf.Array):
            for i in range(len(obj)):
                v = obj[i]
                if isinstance(v, pikepdf.String):
                    b = bytes(v)
                    nb = reg.replace(b, "text")[0]
                    if nb != b:
                        obj[i] = pikepdf.String(nb)
                elif isinstance(v, (pikepdf.Array, pikepdf.Dictionary)) and not v.is_indirect:
                    _pdf_walk(v, reg, pdf, notes, where, depth + 1)
    except Exception:
        pass


def _rw_utf16be(b: bytes, reg: Registry) -> bytes:
    try:
        s = b[2:].decode("utf-16-be")
    except UnicodeDecodeError:
        return b
    ns = reg.replace(s.encode("utf-16-le"), "text")[0].decode("utf-16-le")
    return b"\xfe\xff" + ns.encode("utf-16-be") if ns != s else b


def _rw_content(raw: bytes, reg: Registry) -> bytes:
    """Replace identifiers inside PDF content-stream string literals.  Also
    handles text split by kerning arrays: [(Tom) -20 (Kramer)] is joined for
    matching and the replacement is redistributed over the same pieces."""
    if b"(" not in raw and b"<" not in raw:
        return reg.replace(raw, "text")[0]
    out = reg.replace(raw, "text")[0]
    # kerning arrays
    def fix_array(m):
        arr = m.group(0)
        parts = list(re.finditer(rb"\((?:\\.|[^\\)])*\)", arr))
        if len(parts) < 2:
            return arr
        pieces = [p.group(0)[1:-1] for p in parts]
        if any(b"\\" in p for p in pieces):
            return arr
        joined = b"".join(pieces)
        nj = reg.replace(joined, "text")[0]
        if nj == joined:
            return arr
        res = bytearray(arr)
        pos = 0
        for p, piece in zip(parts, pieces):
            s, e = p.start() + 1, p.end() - 1
            res[s:e] = nj[pos:pos + len(piece)]
            pos += len(piece)
        return bytes(res)
    out = re.sub(rb"\[(?:[^\[\]]{0,4000})\]\s*TJ", fix_array, out)
    return out


# ============================================================ zip family
def rw_zip(data: bytes, reg: Registry, where: str, notes: list, rename=True):
    try:
        zin = zipfile.ZipFile(io.BytesIO(data))
        infos = zin.infolist()
    except Exception:
        notes.append("zip-open-failed")
        return rw_bin(data, reg)
    out = io.BytesIO()
    names = set()
    try:
        with zipfile.ZipFile(out, "w", allowZip64=True) as zo:
            for info in infos:
                raw = b"" if info.is_dir() else zin.read(info)
                nn = info.filename
                if rename:
                    nn = "/".join(_map_component(c, reg, i == len(info.filename.split("/")) - 1 and not info.is_dir())
                                  for i, c in enumerate(info.filename.split("/")))
                if nn in names:
                    nn = info.filename
                names.add(nn)
                new = raw
                if raw:
                    new, sub = transform(where + "!" + info.filename, raw, reg)
                    notes += [f"{info.filename}:{x}" for x in sub]
                zi = zipfile.ZipInfo(nn, date_time=info.date_time)
                zi.compress_type = info.compress_type if info.compress_type in (0, 8, 12, 14) else 8
                zi.external_attr = info.external_attr
                zi.create_system = info.create_system
                zi.comment = reg.replace(info.comment, "text")[0] if info.comment else b""
                zi.extra = b""
                zo.writestr(zi, new)
            if zin.comment:
                zo.comment = reg.replace(zin.comment, "text")[0]
    except Exception as e:
        notes.append(f"zip-rewrite-failed:{str(e)[:80]}")
        return rw_bin(data, reg)
    return out.getvalue()


def rw_tar(data: bytes, reg: Registry, where: str, notes: list):
    try:
        tin = tarfile.open(fileobj=io.BytesIO(data))
        members = tin.getmembers()
    except Exception:
        notes.append("tar-open-failed")
        return rw_bin(data, reg)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tin.format or tarfile.GNU_FORMAT) as to:
        for m in members:
            nm = "/".join(_map_component(c, reg, i == len(m.name.split("/")) - 1 and m.isfile()) for i, c in enumerate(m.name.split("/")))
            m2 = tarfile.TarInfo(nm)
            for attr in ("mode", "mtime", "type", "linkname", "uid", "gid"):
                setattr(m2, attr, getattr(m, attr))
            m2.uname = reg.replace_str(m.uname, "path") if m.uname else ""
            m2.gname = reg.replace_str(m.gname, "path") if m.gname else ""
            if m.isfile():
                raw = tin.extractfile(m).read()
                new, sub = transform(where + "!" + m.name, raw, reg)
                notes += [f"{m.name}:{x}" for x in sub]
                m2.size = len(new)
                to.addfile(m2, io.BytesIO(new))
            else:
                to.addfile(m2)
    return out.getvalue()


def _lzw_compress(data: bytes) -> bytes:
    """Unix compress(1) .Z writer (LZW, 9..16 bits, block mode)."""
    p = subprocess.run(["compress", "-c"], input=data, capture_output=True) if _have("compress") else None
    if p is not None and p.returncode in (0, 2) and p.stdout:
        return p.stdout
    # pure python fallback
    maxbits = 16
    out = bytearray(b"\x1f\x9d" + bytes([0x80 | maxbits]))
    dic = {bytes([i]): i for i in range(256)}
    nxt = 257
    nbits = 9
    bitbuf = 0
    bitcnt = 0
    codes_in_group = 0
    w = b""

    def emit(code):
        nonlocal bitbuf, bitcnt, codes_in_group
        bitbuf |= code << bitcnt
        bitcnt += nbits
        while bitcnt >= 8:
            out.append(bitbuf & 0xFF)
            bitbuf >>= 8
            bitcnt -= 8
        codes_in_group += 1

    def align():
        # ncompress reads codes in groups of 8 per bit width; pad the group
        nonlocal bitbuf, bitcnt, codes_in_group
        if codes_in_group % 8:
            pad = (8 - codes_in_group % 8) * nbits
            bitcnt += pad
            while bitcnt >= 8:
                out.append(bitbuf & 0xFF)
                bitbuf >>= 8
                bitcnt -= 8
        codes_in_group = 0

    for i in range(len(data)):
        c = data[i:i + 1]
        wc = w + c
        if wc in dic:
            w = wc
            continue
        emit(dic[w])
        if nxt < (1 << maxbits):
            dic[wc] = nxt
            nxt += 1
            if nxt > (1 << nbits) and nbits < maxbits:
                align()
                nbits += 1
        w = c
    if w:
        emit(dic[w])
    if bitcnt:
        out.append(bitbuf & 0xFF)
    return bytes(out)


_HAVE = {}


def _have(cmd):
    if cmd not in _HAVE:
        import shutil
        _HAVE[cmd] = shutil.which(cmd) is not None
    return _HAVE[cmd]


def rw_single(kind: str, data: bytes, reg: Registry, where: str, notes: list):
    try:
        if kind == "gz":
            raw = gzip.decompress(data)
        elif kind == "bz2":
            raw = bz2.decompress(data)
        elif kind == "xz":
            raw = lzma.decompress(data)
        elif kind == "Z":
            p = subprocess.run(["gzip", "-dc"], input=data, capture_output=True)
            raw = p.stdout
            if not raw:
                raise ValueError("Z decode failed")
    except Exception:
        notes.append(f"{kind}-decode-failed")
        return data
    inner = where[:-3] if where.lower().endswith((".gz", ".xz")) else where.rsplit(".", 1)[0]
    if len(raw) > 262 and raw[257:262] == b"ustar":
        new = rw_tar(raw, reg, inner, notes)
    else:
        new, sub = transform(inner, raw, reg)
        notes += sub
    if new == raw:
        return data
    if kind == "gz":
        # keep the original header's file name field mapped
        return _gzip_like(data, new, reg)
    if kind == "bz2":
        return bz2.compress(new)
    if kind == "xz":
        return lzma.compress(new)
    if kind == "Z":
        out = _lzw_compress(new)
        chk = subprocess.run(["gzip", "-dc"], input=out, capture_output=True).stdout
        if chk != new:
            notes.append("Z-recompress-failed")
            return gzip.compress(new) if False else data
        return out


def _gzip_like(orig: bytes, new: bytes, reg: Registry) -> bytes:
    flg = orig[3]
    fname = b""
    if flg & 8:
        p = 10
        if flg & 4:
            xlen = struct.unpack_from("<H", orig, 10)[0]
            p += 2 + xlen
        e = orig.index(b"\0", p)
        fname = reg.replace(orig[p:e], "path")[0]
    mtime = struct.unpack_from("<I", orig, 4)[0]
    co = zlib.compressobj(6, zlib.DEFLATED, -15)
    body = co.compress(new) + co.flush()
    hdr = b"\x1f\x8b\x08" + bytes([8 if fname else 0]) + struct.pack("<I", mtime) + b"\x02\xff" + (fname + b"\0" if fname else b"")
    return hdr + body + struct.pack("<II", zlib.crc32(new), len(new) & 0xFFFFFFFF)


def rw_7z(data: bytes, reg: Registry, where: str, notes: list):
    try:
        import py7zr
        items = list(formats.sevenzip_members(data))
        out = io.BytesIO()
        with py7zr.SevenZipFile(out, "w") as zo:
            for name, raw in items:
                nn = "/".join(_map_component(c, reg, i == len(name.split("/")) - 1) for i, c in enumerate(name.split("/")))
                new, sub = transform(where + "!" + name, raw, reg)
                notes += [f"{name}:{x}" for x in sub]
                zo.writestr(new, nn)
        return out.getvalue()
    except Exception as e:
        notes.append(f"7z-rewrite-failed:{str(e)[:60]}")
        return rw_bin(data, reg)


# ================================================================ images
def rw_png(data: bytes, reg: Registry):
    out = bytearray(data[:8])
    p = 8
    try:
        while p + 8 <= len(data):
            ln, typ = struct.unpack_from(">I4s", data, p)
            body = data[p + 8:p + 8 + ln]
            if typ in (b"tEXt", b"iTXt", b"zTXt", b"eXIf"):
                if typ == b"zTXt":
                    k, rest = body.split(b"\0", 1)
                    txt = zlib.decompress(rest[1:])
                    body = k + b"\0" + rest[:1] + zlib.compress(reg.replace(txt, "text")[0])
                else:
                    body = reg.replace(body, "text")[0]
            out += struct.pack(">I", len(body)) + typ + body + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF)
            p += 12 + ln
            if typ == b"IEND":
                break
        out += data[p:]
        return bytes(out)
    except Exception:
        return data


def rw_jpg(data: bytes, reg: Registry):
    # APPn segments (EXIF, XMP, comments) only; same length
    buf = bytearray(data)
    p = 2
    while p + 4 <= len(data) and data[p] == 0xFF:
        m = data[p + 1]
        if m in (0xD9, 0xDA):
            break
        ln = struct.unpack_from(">H", data, p + 2)[0]
        if 0xE0 <= m <= 0xEF or m == 0xFE:
            seg = data[p + 4:p + 2 + ln]
            buf[p + 4:p + 2 + ln] = reg.replace(seg, "text")[0]
        p += 2 + ln
    return bytes(buf)


# ================================================================ dispatch
def transform(where: str, data: bytes, reg: Registry, depth=0):
    """Return (new_bytes, notes)."""
    notes: list = []
    if not data:
        return data, notes
    kind = formats.sniff(where, data)
    if depth == 0 or True:
        lreg, found = local_registry(reg, where, data) if len(data) < 300_000_000 and kind not in ("zip", "7z", "tar", "gz", "Z", "bz2", "xz") else (reg, [])
    try:
        if kind in ("text", "step", "iges"):
            new = rw_text(data, lreg)
            new = rw_step_fields(new, lreg) if kind == "step" else new
        elif kind == "ole":
            new = rw_ole(data, lreg, where, notes)
        elif kind == "sw3d":
            new = rw_sw3d(data, lreg, notes)
        elif kind == "pdf":
            new = rw_pdf(data, lreg, where, notes)
        elif kind == "zip":
            new = rw_zip(data, reg, where, notes)
        elif kind == "tar":
            new = rw_tar(data, reg, where, notes)
        elif kind in ("gz", "bz2", "xz", "Z"):
            new = rw_single(kind, data, reg, where, notes)
        elif kind == "7z":
            new = rw_7z(data, reg, where, notes)
        elif kind == "rar":
            notes.append("rar-kept-binary-rewrite")
            new = rw_bin(data, reg)
        elif kind == "png":
            new = rw_png(data, lreg)
        elif kind == "jpg":
            new = rw_jpg(data, lreg)
        else:
            new = rw_bin(data, lreg)
    except Exception as e:
        notes.append(f"error:{type(e).__name__}:{str(e)[:80]}")
        try:
            new = rw_bin(data, reg)
        except Exception:
            new = data
    return new, notes


def rw_step_fields(data: bytes, reg: Registry) -> bytes:
    """Make sure STEP FILE_NAME author/organization strings are pseudonymized
    even when the value is a common word."""
    m = re.search(rb"FILE_NAME\s*\(", data[:20000])
    if not m:
        return data
    end = data.find(b";", m.end())
    seg = data[m.start():end]
    body = re.sub(rb"/\*.*?\*/", lambda x: b" " * len(x.group()), seg, flags=re.S)
    groups = list(re.finditer(rb"\(([^()]*)\)", body[len(b"FILE_NAME("):]))
    buf = bytearray(data)
    base = m.start() + len(b"FILE_NAME(")
    for g in groups[:2]:
        for s in re.finditer(rb"'((?:[^']|'')*)'", g.group(1)):
            v = s.group(1)
            txt = v.decode("latin-1")
            if txt.strip() and not is_placeholder(clean(txt)) and not re.search(r"open cascade|spatial", txt, re.I):
                a = base + g.start(1) + s.start(1)
                buf[a:a + len(v)] = pseudo_bytes("id", norm_key(txt), v, "latin-1")
    return bytes(buf)
