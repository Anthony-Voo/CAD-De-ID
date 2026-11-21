"""Check rewritten files still open and look for leftover identifiers.

  python3 -m deid.validate --log run.jsonl --out cleaned-corpus --local input --registry registry.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import zipfile

from . import formats


def check(kind, data):
    if kind == "ole":
        import olefile
        o = olefile.OleFileIO(io.BytesIO(data))
        n = len(o.listdir())
        for s in o.listdir():
            o.openstream(s).read()
        return n
    if kind == "pdf":
        import pikepdf
        with pikepdf.open(io.BytesIO(data)) as p:
            return len(p.pages)
    if kind == "zip":
        z = zipfile.ZipFile(io.BytesIO(data))
        bad = z.testzip()
        if bad:
            raise ValueError("bad member " + bad)
        return len(z.infolist())
    if kind == "sw3d":
        return sum(1 for _ in formats.sw3d_entries(data))
    if kind in ("gz", "Z", "bz2", "xz", "tar", "7z"):
        return sum(1 for _ in formats.archive_members(kind, data))
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--local", required=True)
    ap.add_argument("--registry")
    a = ap.parse_args()
    probs = collections.Counter()
    ex = collections.defaultdict(list)
    leaks = collections.Counter()
    leakfiles = collections.defaultdict(set)
    reg = None
    if a.registry:
        from .registry import Registry
        reg = Registry.from_json(a.registry)
    for line in open(a.log):
        d = json.loads(line)
        if "err" in d:
            probs["fetch-error"] += 1
            continue
        src = open(os.path.join(a.local, d["p"]), "rb").read()
        dst = open(os.path.join(a.out, d["np"]), "rb").read()
        k = formats.sniff(d["p"], src)
        k2 = formats.sniff(d["np"], dst)
        if k != k2:
            probs[f"kind-changed:{k}->{k2}"] += 1
            ex[f"kind-changed:{k}->{k2}"].append(d["p"])
        if k in ("text", "step", "iges", "bin", "ole", "sw3d", "jpg") and len(src) != len(dst):
            probs[f"size-changed:{k}"] += 1
            ex[f"size-changed:{k}"].append(d["p"])
        try:
            a1 = check(k, src)
        except Exception:
            a1 = "src-broken"
        try:
            a2 = check(k2, dst)
        except Exception as e:
            a2 = f"broken:{type(e).__name__}"
        if a1 != "src-broken" and a1 != a2:
            probs[f"structure:{k}"] += 1
            ex[f"structure:{k}"].append((d["p"], a1, a2))
        if a1 == "src-broken":
            probs[f"source-defect:{k}"] += 1
        if reg is not None and k in ("text", "step", "iges"):
            for s, e, val in reg.find(dst, "text"):
                leaks[val[0]] += 1
                leakfiles[(val[0], dst[s:e].decode("latin-1"))].add(d["p"])
    print("problems:", dict(probs))
    for k, v in ex.items():
        print(k, v[:4])
    if leaks:
        print("leaks by kind (text files, should be 0):", dict(leaks))
        for k, v in list(leakfiles.items())[:20]:
            print("  ", k, list(v)[:2])


if __name__ == "__main__":
    main()
