"""Font-aware rewriting of visible PDF text.

CAD drawing PDFs (SolidWorks, Inventor, ...) mostly use Type0/Identity-H
fonts: strings are 2-byte glyph codes, readable only through the font's
ToUnicode CMap.  We decode every text-showing operator with its font, run
the identity replacement on the decoded text (same length), and encode the
result back with codes of the same font.  When the subset font has no code
for a needed character, a new code is added to the ToUnicode CMap, so
extracted text is correct (the glyph itself renders as the font's .notdef).
"""
from __future__ import annotations

import re

import pikepdf

from .registry import Registry

WIN = {i: chr(i) for i in range(32, 127)}


def _parse_cmap(data: bytes):
    m = {}
    nbytes = 2
    cs = re.search(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>", data)
    if cs:
        nbytes = max(1, len(cs.group(1)) // 2)
    for blk in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk.group(1)):
            try:
                m[int(a, 16)] = bytes.fromhex(b.decode()).decode("utf-16-be", "replace")
            except ValueError:
                pass
    for blk in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
        body = blk.group(1)
        for a, b, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", body):
            lo, hi = int(a, 16), int(b, 16)
            if hi - lo > 65535:
                continue
            if rest.startswith(b"["):
                items = re.findall(rb"<([0-9A-Fa-f]*)>", rest)
                for i, it in enumerate(items):
                    try:
                        m[lo + i] = bytes.fromhex(it.decode()).decode("utf-16-be", "replace")
                    except ValueError:
                        pass
            else:
                try:
                    base = bytes.fromhex(rest[1:-1].decode())
                    s = base.decode("utf-16-be", "replace")
                except ValueError:
                    continue
                if len(s) == 1:
                    for c in range(lo, hi + 1):
                        m[c] = chr(ord(s) + (c - lo)) if ord(s) + (c - lo) < 0x110000 else s
    return m, nbytes


def _write_cmap(m: dict, nbytes: int) -> bytes:
    w = nbytes * 2
    out = [b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
           b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
           b"/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n",
           f"<{'0' * w}> <{'F' * w}>\n".encode(), b"endcodespacerange\n"]
    items = sorted(m.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        out.append(f"{len(chunk)} beginbfchar\n".encode())
        for code, s in chunk:
            out.append(f"<{code:0{w}X}> <{s.encode('utf-16-be').hex().upper()}>\n".encode())
        out.append(b"endbfchar\n")
    out.append(b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    return b"".join(out)


def _glyph_unicode(name: str) -> str:
    try:
        from fontTools.agl import toUnicode
        u = toUnicode(name)
        return u[:1] if u else ""
    except Exception:
        return name if len(name) == 1 else ""


def _simple_base_map(enc_name: str) -> dict:
    codec = {"/MacRomanEncoding": "mac_roman"}.get(enc_name, "cp1252")
    m = {}
    for c in range(32, 256):
        try:
            ch = bytes([c]).decode(codec)
        except UnicodeDecodeError:
            continue
        if ch.isprintable():
            m[c] = ch
    return m


_CID_CACHE: dict = {}


def _cid_font_map(font) -> dict:
    # content-based key: identical font program + CID->GID map give an
    # identical character map, whatever document they come from
    try:
        import hashlib
        desc = font.DescendantFonts[0]
        ff = desc.FontDescriptor.get("/FontFile2")
        c2g = desc.get("/CIDToGIDMap")
        h = hashlib.md5(ff.read_raw_bytes() if ff is not None else b"")
        if isinstance(c2g, pikepdf.Stream):
            h.update(c2g.read_raw_bytes())
        key = h.hexdigest()
    except Exception:
        key = None
    if key is not None and key in _CID_CACHE:
        return dict(_CID_CACHE[key])
    m = _cid_font_map_uncached(font)
    if key is not None:
        if len(_CID_CACHE) > 256:
            _CID_CACHE.clear()
        _CID_CACHE[key] = m
    return dict(m)


def _cid_font_map_uncached(font) -> dict:
    """CID -> unicode for a Type0 font without ToUnicode, read from the
    embedded TrueType font's cmap (as text extractors do)."""
    import io as _io
    try:
        desc = font.DescendantFonts[0]
        fd = desc.FontDescriptor
        ff = fd.get("/FontFile2")
        if ff is None:
            return {}
        from fontTools.ttLib import TTFont
        tt = TTFont(_io.BytesIO(ff.read_bytes()), lazy=True, fontNumber=0)
        cmap = tt.getBestCmap() or {}
        gid_uni = {}
        for uni, gname in cmap.items():
            try:
                gid = tt.getGlyphID(gname)
            except Exception:
                continue
            gid_uni.setdefault(gid, chr(uni))
        c2g = desc.get("/CIDToGIDMap")
        if c2g is None or (isinstance(c2g, pikepdf.Name) and str(c2g) == "/Identity"):
            return dict(gid_uni)
        if isinstance(c2g, pikepdf.Stream):
            raw = c2g.read_bytes()
            out = {}
            for cid in range(len(raw) // 2):
                gid = (raw[2 * cid] << 8) | raw[2 * cid + 1]
                if gid in gid_uni:
                    out[cid] = gid_uni[gid]
            return out
    except Exception:
        return {}
    return {}


class FontCodec:
    def __init__(self, font):
        self.font = font
        self.ok = True
        self.changed = False
        self.from_font = False
        sub = font.get("/Subtype")
        self.two = sub == "/Type0"
        self.map = {}
        self.nbytes = 2 if self.two else 1
        tu = font.get("/ToUnicode")
        if tu is not None and isinstance(tu, pikepdf.Stream):
            try:
                self.map, self.nbytes = _parse_cmap(tu.read_bytes())
            except Exception:
                self.map = {}
            if not self.two:
                # simple fonts always use 1-byte codes, whatever codespace
                # the ToUnicode CMap declares (<0000> <FFFF> is common)
                self.nbytes = 1
                self.map = {c: u for c, u in self.map.items() if c < 256}
        if not self.map:
            if self.two:
                self.map = _cid_font_map(font)
                self.from_font = bool(self.map)
                if not self.map:
                    self.ok = False  # no way to read this CID text
            else:
                enc = font.get("/Encoding")
                base = "/WinAnsiEncoding"
                if isinstance(enc, pikepdf.Name):
                    base = str(enc)
                elif isinstance(enc, pikepdf.Dictionary) and "/BaseEncoding" in enc:
                    base = str(enc.BaseEncoding)
                self.map = _simple_base_map(base)
                if isinstance(enc, pikepdf.Dictionary) and "/Differences" in enc:
                    code = 0
                    for x in enc.Differences:
                        if isinstance(x, int):
                            code = int(x)
                        else:
                            u = _glyph_unicode(str(x)[1:])
                            if u:
                                self.map[code] = u
                            code += 1
        self.rev = {}
        for c, s in sorted(self.map.items(), reverse=True):
            if len(s) == 1:
                self.rev[s] = c

    def decode(self, b: bytes):
        n = self.nbytes
        codes = [int.from_bytes(b[i:i + n], "big") for i in range(0, len(b) - n + 1, n)]
        return codes, "".join(self.map.get(c, "�")[:1] or "�" for c in codes)

    def encode_char(self, ch: str):
        if ch in self.rev:
            return self.rev[ch]
        if not self.two and 32 <= ord(ch) < 127 and ord(ch) not in self.map:
            self.map[ord(ch)] = ch
            self.rev[ch] = ord(ch)
            self.changed = True
            return ord(ch)
        # allocate a new code
        used = set(self.map)
        start = 0xF000 if self.two else 0x80
        lim = 0xFFFF if self.two else 0xFF
        c = start
        while c in used and c < lim:
            c += 1
        if c >= lim:
            return None
        self.map[c] = ch
        self.rev[ch] = c
        self.changed = True
        return c

    def flush(self, pdf):
        if self.changed and (self.two or "/ToUnicode" in self.font or self.from_font):
            self.font.ToUnicode = pdf.make_stream(_write_cmap(self.map, self.nbytes))
        elif self.changed:
            self.font.ToUnicode = pdf.make_stream(_write_cmap(self.map, 1))


def _replace_text(text: str, reg: Registry, loose: bool = False) -> str:
    b = text.encode("utf-16-le")
    nb = reg.replace(b, "text", loose=loose)[0]
    if nb == b:
        # UTF-16 patterns only match ASCII-compatible data well; also try UTF-8
        u = text.encode("utf-8")
        nu = reg.replace(u, "text", loose=loose)[0]
        if nu == u:
            return text
        try:
            nt = nu.decode("utf-8")
        except UnicodeDecodeError:
            return text
        return nt if len(nt) == len(text) else text
    nt = nb.decode("utf-16-le", "replace")
    return nt if len(nt) == len(text) else text


def rewrite_page_text(pdf, page_obj, reg: Registry, depth=0) -> int:
    """Rewrite text in a page or form XObject; returns number of changed ops.

    All text of the page is matched in drawing order: names drawn by several
    Tj/TJ operators, even in separate BT blocks (one word, fragment or glyph
    at a time, common in CAD exports), are joined before matching (a space is
    put between two operators only where letters/digits meet), and each
    changed character is re-encoded with the font of its own operator."""
    res = page_obj.get("/Resources")
    if res is None or depth > 4:
        return 0
    fonts = res.get("/Font") or {}
    codecs = {}
    try:
        ops = [[list(o), op] for o, op in pikepdf.parse_content_stream(page_obj)]
    except Exception:
        return 0
    cur = None
    changed = 0
    block = []  # segments: (op index, array index or None, codec, codes, text)

    def process(segs):
        nonlocal changed
        if not segs:
            return
        segs = [list(sg) for sg in segs]
        # glyph-by-glyph pages carry no space characters: word boundaries are
        # unknown, so distinctive identities (6+ chars) may match inside runs
        singles = sum(1 for sg in segs if len(sg[4].strip()) <= 1)
        loose = singles > 0.6 * len(segs)
        touched = set()
        # pass 1: operators joined as drawn, no inserted spaces ("Okapu"+"u",
        # "U"+"."+" Okapuu"); pass 2: a space between two operators where
        # letters/digits meet (words drawn one per operator)
        for spaced in (False, True):
            parts, starts = [], []
            pos = 0
            prev = None
            for sg in segs:
                oi, t = sg[0], sg[4]
                if spaced and prev is not None:
                    same_tj = prev[0] == oi
                    single = len(prev[4].strip()) <= 1 and len(t.strip()) <= 1
                    pt = prev[4]
                    both_alnum = bool(pt) and bool(t) and pt[-1].isalnum() and t[0].isalnum()
                    if not same_tj and not single and both_alnum:
                        parts.append(" ")
                        pos += 1
                starts.append(pos)
                parts.append(t)
                pos += len(t)
                prev = sg
            text = "".join(parts)
            if not text.strip():
                return
            nt = _replace_text(text, reg, loose=loose)
            if nt == text:
                continue
            for k, sg in enumerate(segs):
                oi, ai, codec, codes, t = sg
                seg_new = nt[starts[k]:starts[k] + len(t)]
                if seg_new == t:
                    continue
                ncodes = []
                for oc, och, nch in zip(codes, t, seg_new):
                    code = oc if och == nch else codec.encode_char(nch)
                    ncodes.append(oc if code is None else code)
                sg[3] = ncodes
                sg[4] = "".join(nch if (oc != nc or och == nch) else och
                                for oc, nc, och, nch in zip(codes, ncodes, t, seg_new))
                touched.add(k)
        for k in touched:
            oi, ai, codec, codes, t = segs[k]
            nb = b"".join(c.to_bytes(codec.nbytes, "big") for c in codes)
            if ai is None:
                ops[oi][0][-1] = pikepdf.String(nb)
            else:
                ops[oi][0][0][ai] = pikepdf.String(nb)
            changed += 1

    for oi, (operands, op) in enumerate(ops):
        opn = str(op)
        if opn == "Tf" and operands:
            name = str(operands[0])
            if name not in codecs:
                f = fonts.get(name)
                try:
                    codecs[name] = FontCodec(f) if f is not None else None
                except Exception:
                    codecs[name] = None
            cur = codecs[name]
        elif opn in ("Tj", "TJ", "'", '"') and cur is not None and cur.ok and operands:
            if opn == "TJ":
                arr = operands[0]
                if isinstance(arr, pikepdf.Array):
                    for ai in range(len(arr)):
                        if isinstance(arr[ai], pikepdf.String):
                            codes, t = cur.decode(bytes(arr[ai]))
                            block.append((oi, ai, cur, codes, t))
            elif isinstance(operands[-1], pikepdf.String):
                codes, t = cur.decode(bytes(operands[-1]))
                block.append((oi, None, cur, codes, t))
        elif opn == "Do" and operands:
            xo = (res.get("/XObject") or {}).get(str(operands[0]))
            if xo is not None and xo.get("/Subtype") == "/Form":
                try:
                    changed += rewrite_page_text(pdf, xo, reg, depth + 1)
                except Exception:
                    pass
    process(block)
    if changed:
        data = pikepdf.unparse_content_stream([(o, op) for o, op in ops])
        if isinstance(page_obj, pikepdf.Page):
            page_obj.obj.Contents = pdf.make_stream(data)
        elif isinstance(page_obj, pikepdf.Stream):
            page_obj.write(data)
        else:
            page_obj.Contents = pdf.make_stream(data)
        for c in codecs.values():
            if c is not None:
                c.flush(pdf)
    return changed
