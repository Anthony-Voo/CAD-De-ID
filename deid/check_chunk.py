"""Validate a finished output chunk without the originals.

For every file of the chunk (from the run log): it must exist, open with a
real parser for its type, keep its byte size where the rewrite is
same-length, and contain no registry identifier (binary-safe scan).

  python3 -m deid.check_chunk --log run.jsonl --out chunk --registry registry.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os

from . import formats
from .registry import Registry
from .validate import check

SAME_LEN = {"text", "step", "iges", "bin", "ole", "sw3d", "jpg"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--registry", required=True)
    a = ap.parse_args()
    reg = Registry.from_json(a.registry)
    stats = collections.Counter()
    problems = collections.defaultdict(list)
    leaks = collections.Counter()
    leak_ex = collections.defaultdict(set)
    for line in open(a.log):
        d = json.loads(line)
        p = os.path.join(a.out, d["np"])
        if not os.path.exists(p):
            continue  # file belongs to another (already uploaded) chunk
        stats["files"] += 1
        data = open(p, "rb").read()
        k = formats.sniff(d["np"], data)
        stats[k] += 1
        if d.get("n") is not None and k in SAME_LEN and len(data) != d["n"] and "duplicate" not in d.get("notes", []):
            problems["size-changed:" + k].append(d["np"])
        if any(n.startswith(("7z-", "zip-", "gz-", "Z-", "pdf-open", "ole-open", "tar-open")) for n in d.get("notes", [])):
            stats["source-defect"] += 1
        else:
            try:
                check(k, data)
            except Exception as e:
                problems[f"unreadable:{k}:{type(e).__name__}"].append(d["np"])
        for s, e, v in reg.find(data, "bin" if k not in ("text", "step", "iges") else "text"):
            leaks[k] += 1
            if len(leak_ex[k]) < 12:
                leak_ex[k].add(data[s:e].decode("latin-1"))
        if reg.replace_str(d["np"], "path") != d["np"]:
            problems["path-still-identifying"].append(d["np"])
    print("checked:", dict(stats))
    print("problems:", {k: len(v) for k, v in problems.items()})
    for k, v in problems.items():
        print("  ", k, v[:3])
    print("leftover identifier hits by type:", dict(leaks))
    for k, v in leak_ex.items():
        print("  ", k, sorted(v)[:12])


if __name__ == "__main__":
    main()
