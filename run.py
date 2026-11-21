#!/usr/bin/env python3
"""De-identify the Machina corpus (or a slice of it) into OUT/corpus/.

    DEID_KEY=<secret> python3 run.py --manifest manifest.tsv --out OUT [--prefix P] [--limit N]
                                      [--sample N --seed S] [--workers 12]

Reads each file from the public S3 corpus over HTTPS (or --input DIR, a local
copy laid out like the bucket), writes the de-identified file at its renamed
path, and appends to OUT/private/: mapping.tsv (old -> new path), log.tsv
(per-file handler stats and errors), defects.tsv. OUT/private/ holds the
mapping and must never be uploaded; upload only OUT/corpus.
"""
import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deid.identities import Identities, Scrubber  # noqa: E402
from deid.paths import Renamer  # noqa: E402
from deid.process import Processor, Stats  # noqa: E402

S3 = "https://machina-deid-v1-challenge.s3.us-west-2.amazonaws.com/releases/v1/corpus/"
BIG = 200 << 20

_G = {}


def _init(ids_path, input_dir):
    ids = Identities().load(ids_path)
    _G["content"] = Scrubber(ids)
    _G["paths"] = Scrubber(ids, for_paths=True)
    _G["ren"] = Renamer(_G["paths"])
    _G["input"] = input_dir


def fetch(key):
    if _G["input"]:
        return open(os.path.join(_G["input"], key), "rb").read()
    url = S3 + urllib.parse.quote(key)
    for attempt in range(5):
        try:
            return urllib.request.urlopen(url, timeout=120).read()
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))


def defects_of(key, data):
    name = key.rsplit("/", 1)[-1]
    out = []
    if not data:
        out.append("empty (zero-byte) source file")
    if name.startswith("~$"):
        out.append("CAD lock file (~$), not an engineering file")
    if "/_vti_cnf/" in key:
        out.append("FrontPage _vti_cnf metadata stub")
    if name.endswith(".resume-partial"):
        out.append("truncated partial download in the source")
    low = name.lower()
    if low.endswith((".step", ".stp")) and data and not data.lstrip()[:13].startswith(b"ISO-10303-21"):
        out.append("not a valid STEP file (no ISO-10303-21 header)" + (" - actually a ZIP" if data[:2] == b"PK" else ""))
    return out


def work(key, out_root):
    st = Stats()
    ren = _G["ren"]
    newkey = ren.key(key)
    try:
        data = fetch(key)
    except Exception as e:
        return key, newkey, "download_error", {"error": repr(e)}, []
    defects = defects_of(key, data)
    proc = Processor(_G["content"], ren.component, st)
    try:
        new = proc.file(key.rsplit("/", 1)[-1], data) if data else data
        status = "ok"
    except Exception as e:  # never drop a file: generic same-length scrub instead
        new, _ = _G["content"].scrub(data)
        status = "fallback"
        st.add("error:" + type(e).__name__)
    dst = os.path.join(out_root, "corpus", newkey)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(new)
    st.c["changed"] = int(new != data)
    return key, newkey, status, st.c, defects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids", default=None, help="identity list (default: OUT/private/ids.json, built from the manifest)")
    ap.add_argument("--input", default=None, help="local copy of the corpus instead of S3")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--shard", default="", help="I/N: only files whose path hash falls in shard I of N")
    a = ap.parse_args()
    if not os.environ.get("DEID_KEY"):
        sys.exit("set DEID_KEY (the secret pseudonym key; keep it out of the submission)")

    rows = [l.rstrip("\n").split("\t") for l in open(a.manifest, encoding="utf-8")]
    priv = os.path.join(a.out, "private")
    os.makedirs(priv, exist_ok=True)
    ids_path = a.ids or os.path.join(priv, "ids.json")
    if not os.path.exists(ids_path):
        ids = Identities()
        ids.from_manifest([r[0] for r in rows])
        ids.save(ids_path)
    sel = [r for r in rows if r[0].startswith(a.prefix)]
    if a.shard:
        i, n = map(int, a.shard.split("/"))
        # shard by the top-level repo/site folder so duplicates and X.extracted twins stay together
        def top(k):
            return "/".join(k.split("/")[:3])
        sel = [r for r in sel if int(hashlib.md5(top(r[0]).encode()).hexdigest(), 16) % n == i]
    if a.sample:
        sel = random.Random(a.seed).sample(sel, min(a.sample, len(sel)))
    if a.limit:
        sel = sel[: a.limit]

    # group byte-identical files (same etag + size): process one, copy the rest
    groups = {}
    for key, size, etag in sel:
        groups.setdefault((etag, size), []).append(key)
    firsts = [(ks[0], int(sz)) for (et, sz), ks in groups.items()]
    small = [k for k, s in firsts if s < BIG]
    big = [k for k, s in firsts if s >= BIG]
    done = {}
    t0 = time.time()
    logf = open(os.path.join(priv, "log.tsv"), "a", encoding="utf-8")
    mapf = open(os.path.join(priv, "mapping.tsv"), "a", encoding="utf-8")
    deff = open(os.path.join(priv, "defects.tsv"), "a", encoding="utf-8")

    def record(res):
        key, newkey, status, stats, defects = res
        done[key] = newkey
        logf.write(f"{key}\t{newkey}\t{status}\t{json.dumps(stats)}\n")
        mapf.write(f"{key}\t{newkey}\n")
        for d in defects:
            deff.write(f"{newkey}\t{d}\n")

    for batch, nworkers in ((small, a.workers), (big, 1)):
        with ProcessPoolExecutor(nworkers, initializer=_init, initargs=(ids_path, a.input)) as ex:
            futs = [ex.submit(work, k, a.out) for k in batch]
            for i, f in enumerate(as_completed(futs), 1):
                record(f.result())
                if i % 500 == 0:
                    print(f"{i}/{len(batch)} files, {time.time() - t0:.0f}s", file=sys.stderr, flush=True)

    # duplicates: copy the processed first copy to each duplicate's renamed path
    _init(ids_path, a.input)
    ndup = 0
    for (et, sz), ks in groups.items():
        src_new = done.get(ks[0])
        for k in ks[1:]:
            newkey = _G["ren"].key(k)
            dst = os.path.join(a.out, "corpus", newkey)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if src_new is not None:
                shutil.copyfile(os.path.join(a.out, "corpus", src_new), dst)
                status = "duplicate_copy"
            else:
                status = "duplicate_of_failed"
            logf.write(f"{k}\t{newkey}\t{status}\t{{}}\n")
            mapf.write(f"{k}\t{newkey}\n")
            deff.write(f"{newkey}\tbyte-identical duplicate of {src_new}\n")
            ndup += 1
    print(f"done: {len(done)} processed, {ndup} duplicates copied, {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
