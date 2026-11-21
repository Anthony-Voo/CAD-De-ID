"""Pass 1: stream every corpus file from the mirror (or a local copy) and harvest
identity values.  Writes JSON lines {"p": path, "h": [[kind, value], ...]}.
Resumable: paths already in the output are skipped.

  python3 -m deid.discover --manifest manifest.tsv --out discovered.jsonl \
      [--local ./input] [--jobs 8]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from multiprocessing import Pool

from .harvest import harvest

BASE = os.environ.get("MACHINA_INPUT_BASE", "https://machina-input.joinbuilders.org")
LOCAL = None


def fetch(row):
    path, size, md5, url = row
    if LOCAL:
        with open(os.path.join(LOCAL, path), "rb") as f:
            return f.read()
    if int(size) == 0:
        return b""
    for attempt in range(6):
        p = subprocess.run(["curl", "-fsS", "--retry", "5", "--retry-connrefused", f"{BASE}/{url}"],
                           capture_output=True)
        if p.returncode == 0 and len(p.stdout) == int(size):
            return p.stdout
        time.sleep(2 + attempt * 3)
    raise IOError(f"fetch failed {path}: {p.stderr[-200:]!r}")


def work(row):
    try:
        data = fetch(row)
    except Exception as e:
        return {"p": row[0], "err": str(e)[:300]}
    try:
        if len(data) > 300_000_000:
            # huge files: scan headers and tails only (memory)
            from .harvest import harvest_text, harvest_step
            head, tail = data[:64 << 20], data[-(16 << 20):]
            del data
            h = harvest_step(head, row[0]) + harvest_text(head, row[0]) + harvest_text(tail, row[0])
            return {"p": row[0], "h": sorted({(k, v) for k, v, _ in h}), "n": int(row[1]), "partial": 1}
        h = harvest(row[0], data)
    except Exception as e:
        return {"p": row[0], "h": [], "err": "harvest: " + str(e)[:200]}
    uniq = sorted({(k, v) for k, v, _ in h})
    return {"p": row[0], "h": uniq, "n": len(data)}


def init(local):
    global LOCAL
    LOCAL = local


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--local")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    rows = [l.rstrip("\n").split("\t") for l in open(a.manifest) if not l.startswith("#")]
    done = set()
    if os.path.exists(a.out):
        for l in open(a.out):
            try:
                d = json.loads(l)
                if "err" not in d or d.get("h") is not None:
                    done.add(d["p"])
            except Exception:
                pass
    todo = [r for r in rows if r[0] not in done]
    if a.limit:
        todo = todo[: a.limit]
    # big files last, so they do not all sit in memory together
    todo.sort(key=lambda r: int(r[1]) > 200_000_000)
    print(f"{len(todo)} to do, {len(done)} done", flush=True)
    t0 = time.time()
    nbytes = 0
    with open(a.out, "a") as out, Pool(a.jobs, initializer=init, initargs=(a.local,), maxtasksperchild=200) as pool:
        for i, res in enumerate(pool.imap_unordered(work, todo, chunksize=4)):
            out.write(json.dumps(res, ensure_ascii=False) + "\n")
            nbytes += res.get("n", 0)
            if i % 2000 == 0:
                out.flush()
                dt = time.time() - t0
                print(f"{i}/{len(todo)} {nbytes/1e9:.1f} GB {nbytes/1e6/max(dt,1):.0f} MB/s {dt/60:.1f} min", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
