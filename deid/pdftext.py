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


class FontCodec:
    def __init__(self, font):
        self.font = font
        self.ok = True
        self.changed = False
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
        if not self.map:
            if self.two:
                self.ok = False  # cannot read CID text without ToUnicode
            else:
                self.map = dict(WIN)
                enc = font.get("/Encoding")
                if isinstance(enc, pikepdf.Dictionary) and "/Differences" in enc:
                    code = 0
                    for x in enc.Differences:
                        if isinstance(x, int):
                            code = int(x)
                        else:
                            name = str(x)[1:]
                            if len(name) == 1:
                                self.map[code] = name
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
        if self.changed and (self.two or "/ToUnicode" in self.font):
            self.font.ToUnicode = pdf.make_stream(_write_cmap(self.map, self.nbytes))
        elif self.changed:
            self.font.ToUnicode = pdf.make_stream(_write_cmap(self.map, 1))


def _replace_text(text: str, reg: Registry) -> str:
    b = text.encode("utf-16-le")
    nb = reg.replace(b, "text")[0]
    if nb == b:
        # UTF-16 patterns only match ASCII-compatible data well; also try UTF-8
        u = text.encode("utf-8")
        nu = reg.replace(u, "text")[0]
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
    """Rewrite text in a page or form XObject; returns number of changed ops."""
    res = page_obj.get("/Resources")
    if res is None or depth > 4:
        return 0
    fonts = res.get("/Font") or {}
    codecs = {}
    try:
        ops = pikepdf.parse_content_stream(page_obj)
    except Exception:
        return 0
    cur = None
    changed = 0
    new_ops = []
    for operands, op in ops:
        opn = str(op)
        if opn == "Tf" and operands:
            name = str(operands[0])
            if name not in codecs:
                f = fonts.get(name)
                codecs[name] = FontCodec(f) if f is not None else None
            cur = codecs[name]
        elif opn in ("Tj", "TJ", "'", '"') and cur is not None and cur.ok:
            strs = []
            if opn == "TJ":
                arr = operands[0]
                idx = [i for i in range(len(arr)) if isinstance(arr[i], pikepdf.String)]
                strs = [bytes(arr[i]) for i in idx]
            else:
                k = len(operands) - 1
                strs = [bytes(operands[k])]
            decoded = [cur.decode(s) for s in strs]
            text = "".join(t for _, t in decoded)
            nt = _replace_text(text, reg) if text.strip() else text
            if nt != text:
                pos = 0
                out_strs = []
                okall = True
                for (codes, t), s in zip(decoded, strs):
                    seg_new = nt[pos:pos + len(t)]
                    pos += len(t)
                    nb = bytearray()
                    for oc, och, nch in zip(codes, t, seg_new):
                        if och == nch:
                            code = oc
                        else:
                            code = cur.encode_char(nch)
                            if code is None:
                                okall = False
                                code = oc
                        nb += code.to_bytes(cur.nbytes, "big")
                    out_strs.append(bytes(nb))
                if okall or True:
                    if opn == "TJ":
                        arr = operands[0]
                        for i, nbs in zip(idx, out_strs):
                            arr[i] = pikepdf.String(nbs)
                    else:
                        operands = list(operands)
                        operands[-1] = pikepdf.String(out_strs[0])
                    changed += 1
        elif opn == "Do" and operands:
            xo = (res.get("/XObject") or {}).get(str(operands[0]))
            if xo is not None and xo.get("/Subtype") == "/Form":
                try:
                    changed += rewrite_page_text(pdf, xo, reg, depth + 1)
                except Exception:
                    pass
        new_ops.append((operands, op))
    if changed:
        data = pikepdf.unparse_content_stream(new_ops)
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
