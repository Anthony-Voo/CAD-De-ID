#!/usr/bin/env python3
"""Check that de-identified files are still valid and structurally unchanged.

Usage: validate.py ORIGINAL_DIR PROCESSED_DIR MAPPING.tsv
MAPPING.tsv lines: original_relpath<TAB>processed_relpath
Prints one line per problem and a summary; exit 1 if anything broke.
"""
import io
import sys
import zipfile
from collections import Counter
from pathlib import Path


def check(orig: bytes, new: bytes, name: str):
    low = name.lower()
    if orig[:4] == b"PK\x03\x04":
        z0, z1 = zipfile.ZipFile(io.BytesIO(orig)), zipfile.ZipFile(io.BytesIO(new))
        if z1.testzip() is not None:
            return "zip CRC error"
        if len(z0.infolist()) != len(z1.infolist()):
            return f"zip members {len(z0.infolist())} -> {len(z1.infolist())}"
        return None
    if orig[:5] == b"%PDF-":
        import pikepdf
        try:
            n0 = len(pikepdf.open(io.BytesIO(orig)).pages)
        except Exception:
            return None  # source PDF already broken: not our regression
        try:
            n1 = len(pikepdf.open(io.BytesIO(new)).pages)
        except Exception as e:
            return f"pdf no longer opens: {e}"
        return None if n0 == n1 else f"pdf pages {n0} -> {n1}"
    if orig[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        import olefile
        try:
            s0 = olefile.OleFileIO(orig).listdir()
        except Exception:
            return None
        try:
            s1 = olefile.OleFileIO(new).listdir()
        except Exception as e:
            return f"ole no longer opens: {e}"
        return None if len(s0) == len(s1) else f"ole streams {len(s0)} -> {len(s1)}"
    if orig[:8] == b"\x89PNG\r\n\x1a\n":
        from PIL import Image
        try:
            Image.open(io.BytesIO(new)).load()
        except Exception as e:
            return f"png broken: {e}"
        return None
    if orig[:2] == b"\x1f\x8b":
        import gzip
        try:
            gzip.decompress(new)
        except Exception as e:
            return f"gzip broken: {e}"
        return None
    if low.endswith((".step", ".stp")):
        if new.count(b";") != orig.count(b";") or b"ENDSEC;" not in new[:400000]:
            return "step structure changed"
        return None
    if len(new) != len(orig):
        return f"size {len(orig)} -> {len(new)} (same-length scrub expected)"
    return None


def main():
    od, pd, mp = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    bad, kinds = 0, Counter()
    for line in open(mp, encoding="utf-8"):
        a, b = line.rstrip("\n").split("\t")
        orig, new = (od / a).read_bytes(), (pd / b).read_bytes()
        kinds["changed" if orig != new else "unchanged"] += 1
        try:
            err = check(orig, new, a)
        except Exception as e:
            err = f"check crashed: {type(e).__name__}: {e}"
        if err:
            bad += 1
            print(f"BROKEN {a}: {err}")
    print(f"{sum(kinds.values())} files, {dict(kinds)}, broken {bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
