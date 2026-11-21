"""Pass 2 runner: fetch -> transform -> write under OUT/<mapped path>.

  python3 -m deid.run --manifest manifest.tsv --registry registry.json --out cleaned-corpus \
      [--local ./input] [--jobs 8] [--prefix sources/github/] [--log run.jsonl]

Resumable through the log: paths already logged as written are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from multiprocessing import Pool

from .discover import fetch, init as dinit
from .registry import Registry
from .rewrite import map_path, transform

REG = None
OUT = None
PATHMAP = None


def init(reg_path, out, local, pathmap):
    global REG, OUT, PATHMAP
    REG = Registry.from_json(reg_path)
    OUT = out
    PATHMAP = pathmap
    dinit(local)


def source_defects(path, data):
    out = []
    name = path.rsplit("/", 1)[-1]
    if name.startswith("~$"):
        out.append("defect-lockfile")
    if "/_vti_cnf/" in path or "/_vti_pvt/" in path:
        out.append("defect-vti")
    if name.endswith(".resume-partial"):
        out.append("defect-partial-download")
    from .formats import ext_of, sniff
    e = ext_of(path)
    k = sniff(path, data) if data else "empty"
    if e in ("step", "stp") and data and k != "step":
        out.append("defect-step-not-iso" if k in ("text", "bin") else f"defect-step-is-{k}")
    if e == "pdf" and data and k != "pdf":
        out.append("defect-pdf-not-pdf")
    return out


def work(row):
    path = row[0]
    newp = PATHMAP[path]
    t = time.time()
    try:
        data = fetch(row)
    except Exception as e:
        return {"p": path, "np": newp, "err": "fetch:" + str(e)[:200]}
    try:
        new, notes = transform(path, data, REG)
    except Exception as e:
        new, notes = data, [f"fatal:{type(e).__name__}:{str(e)[:100]}"]
    notes = source_defects(path, data) + notes
    dst = os.path.join(OUT, newp)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part~"
    with open(tmp, "wb") as f:
        f.write(new)
    os.replace(tmp, dst)
    return {"p": path, "np": newp, "n": len(data), "m": len(new), "chg": new != data,
            "notes": notes[:20], "s": round(time.time() - t, 2),
            "md5in": hashlib.md5(data).hexdigest()}


def build_pathmap(rows, reg):
    pm = {}
    used = {}
    for r in rows:
        np_ = map_path(r[0], reg)
        low = np_
        if low in used and used[low] != r[0]:
            stem, dot, ext = np_.rpartition(".")
            np_ = f"{stem}-{hashlib.md5(r[0].encode()).hexdigest()[:6]}.{ext}" if dot else np_ + "-dup"
        used[np_] = r[0]
        pm[r[0]] = np_
    return pm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--local")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--prefix", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--log", required=True)
    ap.add_argument("--pathmap-out")
    ap.add_argument("--budget-gb", type=float, default=0, help="stop after this much input (chunked runs)")
    a = ap.parse_args()
    rows = [l.rstrip("\n").split("\t") for l in open(a.manifest) if not l.startswith("#")]
    reg = Registry.from_json(a.registry)
    pm = build_pathmap(rows, reg)
    if a.pathmap_out:
        with open(a.pathmap_out, "w") as f:
            for k, v in pm.items():
                f.write(f"{k}\t{v}\n")
    done = set()
    if os.path.exists(a.log):
        for l in open(a.log):
            try:
                d = json.loads(l)
                if "err" not in d:
                    done.add(d["p"])
            except Exception:
                pass
    from .formats import ext_of
    groups = {}
    for r in rows:
        if r[2]:
            groups.setdefault((r[2], r[1], ext_of(r[0])), []).append(r[0])
    leader_of = {}
    followers = {}
    for g in groups.values():
        if len(g) > 1:
            followers[g[0]] = g[1:]
            for f in g[1:]:
                leader_of[f] = g[0]
    todo = [r for r in rows if r[0].startswith(a.prefix) and r[0] not in leader_of
            and (r[0] not in done or any(f not in done for f in followers.get(r[0], [])))]
    if a.limit:
        todo = todo[: a.limit]
    if a.budget_gb:
        sel, tot = [], 0
        for r in todo:
            if tot + int(r[1]) > a.budget_gb * 1e9 and sel:
                break
            sel.append(r)
            tot += int(r[1])
        todo = sel
    todo.sort(key=lambda r: int(r[1]) > 200_000_000)
    print(f"{len(todo)} to do, {len(done)} done", flush=True)
    t0 = time.time()
    nb = 0
    with open(a.log, "a") as log, Pool(a.jobs, initializer=init, initargs=(a.registry, a.out, a.local, pm), maxtasksperchild=100) as pool:
        for i, res in enumerate(pool.imap_unordered(work, todo, chunksize=2)):
            log.write(json.dumps(res, ensure_ascii=False) + "\n")
            if "err" not in res:
                src = os.path.join(a.out, res["np"])
                for f in followers.get(res["p"], []):
                    dst = os.path.join(a.out, pm[f])
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.exists(dst):
                        os.unlink(dst)
                    try:
                        os.link(src, dst)
                    except OSError:
                        import shutil
                        shutil.copyfile(src, dst)
                    log.write(json.dumps({"p": f, "np": pm[f], "n": res.get("n"), "m": res.get("m"),
                                          "chg": res.get("chg"), "notes": ["duplicate"], "dup_of": res["np"],
                                          "md5in": res.get("md5in")}, ensure_ascii=False) + "\n")
            nb += res.get("n", 0)
            if i % 1000 == 0:
                log.flush()
                dt = time.time() - t0
                print(f"{i}/{len(todo)} {nb/1e9:.1f} GB {nb/1e6/max(dt,1):.1f} MB/s {dt/60:.1f} min", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
