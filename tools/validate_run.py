#!/usr/bin/env python3
"""Validate a run: re-fetch each original from S3 and check its output with validate.check.

Usage: validate_run.py OUT [--sample N]
"""
import os, random, sys, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import check
S3 = "https://machina-deid-v1-challenge.s3.us-west-2.amazonaws.com/releases/v1/corpus/"
out = sys.argv[1]
rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(out, "private", "mapping.tsv"), encoding="utf-8")]
if "--sample" in sys.argv:
    rows = random.Random(1).sample(rows, min(int(sys.argv[sys.argv.index("--sample") + 1]), len(rows)))
def one(r):
    k, nk = r
    orig = urllib.request.urlopen(S3 + urllib.parse.quote(k), timeout=120).read()
    new = open(os.path.join(out, "corpus", nk), "rb").read()
    try:
        err = check(orig, new, k)
    except Exception as e:
        err = f"check crashed: {type(e).__name__}: {e}"
    return k, err, orig != new
bad = changed = 0
with ThreadPoolExecutor(16) as ex:
    for k, err, ch in ex.map(one, rows):
        changed += ch
        if err:
            bad += 1; print(f"BROKEN {k[:100]}: {err}")
print(f"{len(rows)} files, changed {changed}, broken {bad}")
