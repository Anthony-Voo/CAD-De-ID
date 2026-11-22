"""Local measurement of PDF de-identification quality (no scorer needed).

For a set of PDFs (originals on disk), run transform() and measure:

  leaks        original identifiers (registry + per-file harvested values)
               still present in ANY text surface of the output PDF: page text
               (pdftotext), Info dictionary, XMP, annotations, form fields,
               bookmarks, embedded file names and contents.
  consistency  for every original identity (normalized key), the set of
               distinct pseudonym strings it became across all PDFs
               (aligned page text + metadata fields); ideal = 1.
  collisions   pseudonym strings that came from 2+ different identities.

  python3 -m deid.measure --rows pdfsample.tsv --input input3 --registry registry.json
"""
from __future__ import annotations

import argparse
import collections
import io
import re
import subprocess

import pikepdf

from .harvest import harvest, clean, is_placeholder
from .pseudo import norm_key
from .registry import Registry
from .rewrite import transform


def pdf_surfaces(data: bytes) -> dict:
    """Every text surface of a PDF, as one string per surface name."""
    out = {}
    try:
        out["text"] = subprocess.run(["pdftotext", "-q", "-", "-"], input=data, capture_output=True,
                                     timeout=120).stdout.decode("utf-8", "replace")
    except Exception:
        out["text"] = ""
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except Exception:
        return out
    with pdf:
        try:
            out["info"] = "\n".join(f"{k}={v}" for k, v in pdf.docinfo.items())
        except Exception:
            pass
        try:
            if "/Metadata" in pdf.Root:
                out["xmp"] = pdf.Root.Metadata.read_bytes().decode("utf-8", "replace")
        except Exception:
            pass
        parts = []
        try:
            for page in pdf.pages:
                for a in page.get("/Annots") or []:
                    for k in ("/Contents", "/T", "/TU", "/V", "/NM", "/Subj"):
                        if k in a:
                            parts.append(str(a[k]))
                    act = a.get("/A")
                    if act is not None and "/URI" in act:
                        parts.append(str(act.URI))
        except Exception:
            pass
        out["annots"] = "\n".join(parts)
        parts = []
        try:
            def walk(o, depth=0):
                while o is not None and depth < 500:
                    if "/Title" in o:
                        parts.append(str(o.Title))
                    if "/First" in o:
                        walk(o.First, depth + 1)
                    o = o.get("/Next")
                    depth += 1
            if "/Outlines" in pdf.Root and "/First" in pdf.Root.Outlines:
                walk(pdf.Root.Outlines.First)
        except Exception:
            pass
        out["bookmarks"] = "\n".join(parts)
        parts = []
        try:
            if "/AcroForm" in pdf.Root:
                for f in pdf.Root.AcroForm.get("/Fields") or []:
                    for k in ("/T", "/TU", "/V"):
                        if k in f:
                            parts.append(str(f[k]))
        except Exception:
            pass
        out["forms"] = "\n".join(parts)
        parts = []
        try:
            for name, att in pdf.attachments.items():
                parts.append(name)
                try:
                    parts.append(att.get_file().read_bytes()[:200000].decode("latin-1"))
                except Exception:
                    pass
        except Exception:
            pass
        out["attachments"] = "\n".join(parts)
    return out


def originals_of(path: str, data: bytes, reg: Registry):
    """Identity strings present in the original: registry matches + values
    harvested from the file itself (what the scorer would know)."""
    vals = set()
    for k, v, _ in harvest(path, data):
        v = clean(v)
        if v and not is_placeholder(v) and len(norm_key(v)) >= 3 and k not in ("domain",):
            vals.add(v)
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--outputs", help="read already de-identified files from this folder (same relative paths)")
    a = ap.parse_args()
    reg = Registry.from_json(a.registry)
    rows = [l.split("\t")[0] for l in open(a.rows) if l.split("\t")[0].lower().endswith(".pdf")]
    if a.limit:
        rows = rows[: a.limit]
    leaks = collections.Counter()
    leak_where = collections.Counter()
    ident_to_pseudo = collections.defaultdict(set)
    pseudo_to_ident = collections.defaultdict(set)
    n = 0
    for p in rows:
        try:
            data = open(f"{a.input}/{p}", "rb").read()
        except OSError:
            continue
        n += 1
        if a.outputs:
            try:
                new = open(f"{a.outputs}/{p}", "rb").read()
            except OSError:
                continue
        else:
            new, _ = transform(p, data, reg)
        so, sn = pdf_surfaces(data), pdf_surfaces(new)
        # identities of this file: registry matches in the original surfaces + harvested values
        idents = set()
        for surf, txt in so.items():
            b = txt.encode("utf-8", "replace")
            for s, e, v in reg.find(b, "text"):
                idents.add(b[s:e].decode("utf-8", "replace"))
        idents |= originals_of(p, data, reg)
        # leaks: any original identity string (case-insensitive, word-bounded) in any output surface
        for ident in idents:
            if len(norm_key(ident)) < 3:
                continue
            rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(ident) + r"(?![A-Za-z0-9])", re.I)
            for surf, txt in sn.items():
                if rx.search(txt):
                    leaks[ident] += 1
                    leak_where[surf] += 1
        # consistency: aligned page text (same length) and Info fields
        to, tn = so.get("text", ""), sn.get("text", "")
        if len(to) == len(tn):
            b = to.encode("utf-16-le")
            for s, e, v in reg.find(b, "text"):
                if s % 2 or (e - s) % 2:
                    continue
                o = b[s:e].decode("utf-16-le", "replace")
                ps = tn.encode("utf-16-le")[s:e].decode("utf-16-le", "replace")
                ident_to_pseudo[norm_key(o)].add(ps)
                pseudo_to_ident[ps].add(norm_key(o))
        try:
            po, pn = pikepdf.open(io.BytesIO(data)), pikepdf.open(io.BytesIO(new))
            for k in ("/Author", "/Creator", "/Title", "/Subject", "/Company"):
                if k in po.docinfo and k in pn.docinfo:
                    ov, nv = str(po.docinfo[k]), str(pn.docinfo[k])
                    for part_o, part_n in zip(re.split(r"[\\();,/]+", ov), re.split(r"[\\();,/]+", nv)):
                        part_o, part_n = part_o.strip(), part_n.strip()
                        if part_o and part_o != part_n and len(norm_key(part_o)) >= 3:
                            ident_to_pseudo[norm_key(part_o)].add(part_n)
                            pseudo_to_ident[part_n].add(norm_key(part_o))
        except Exception:
            pass
    multi = {k: v for k, v in ident_to_pseudo.items() if len({x.lower() for x in v}) > 1}
    multi_exact = {k: v for k, v in ident_to_pseudo.items() if len(v) > 1}
    coll = {k: v for k, v in pseudo_to_ident.items() if len(v) > 1}
    print(f"PDFs measured: {n}")
    print(f"LEAKS: {sum(leaks.values())} occurrences of {len(leaks)} identities; by surface {dict(leak_where)}")
    print("   top:", leaks.most_common(a.show))
    print(f"CONSISTENCY: {len(ident_to_pseudo)} identities seen; {len(multi_exact)} with 2+ pseudonyms (exact), "
          f"{len(multi)} ignoring case")
    for k, v in list(multi_exact.items())[: a.show]:
        print("   ", k, "->", sorted(v)[:4])
    print(f"COLLISIONS: {len(coll)} pseudonyms used for 2+ identities")
    for k, v in list(coll.items())[: a.show]:
        print("   ", k, "<-", sorted(v)[:4])


if __name__ == "__main__":
    main()
