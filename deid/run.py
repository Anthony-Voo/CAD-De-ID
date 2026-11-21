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


def path_literals(pathmap):
    """Renamed folder / file-name components -> their new form, for exact
    replacement inside file contents (assembly and drawing references)."""
    lit = {}
    for o, n in pathmap.items():
        oa, na = o.split("/"), n.split("/")
        if len(oa) != len(na):
            continue
        for x, y in zip(oa, na):
            if x != y and len(x) >= 6 and len(x) == len(y):
                lit[x] = y
                if "." in x and "." in y:
                    xs, ys = x.rsplit(".", 1)[0], y.rsplit(".", 1)[0]
                    if xs != ys and len(xs) >= 6:
                        lit[xs] = ys
    return lit


def init(reg_path, out, local, pathmap, mem_gb=2.0):
    global REG, OUT, PATHMAP
    try:
        import resource
        lim = int(mem_gb * (1 << 30))
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:
        pass
    REG = Registry.from_json(reg_path)
    REG.set_literals(path_literals(pathmap))
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
    except MemoryError:
        return {"p": path, "np": newp, "err": "memory"}
    except Exception as e:
        new, notes = data, [f"fatal:{type(e).__name__}:{str(e)[:100]}"]
    if any("MemoryError" in n for n in notes):
        return {"p": path, "np": newp, "err": "memory"}
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


def _zip_stream_rewrite(src, dst_tmp, reg):
    """Zip-family file too large for memory: rebuild it member by member,
    each member streamed in blocks (same-length replacement, overlapping
    blocks so no match is cut) and recompressed, so CRCs stay valid."""
    import zipfile
    from .rewrite import _map_component
    B, OV = 32 << 20, 4096
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst_tmp, "w", allowZip64=True) as zo:
        for info in zin.infolist():
            parts = info.filename.split("/")
            nn = "/".join(_map_component(c, reg, i == len(parts) - 1 and not info.is_dir()) for i, c in enumerate(parts))
            zi = zipfile.ZipInfo(nn, date_time=info.date_time)
            zi.compress_type = info.compress_type if info.compress_type in (0, 8) else 8
            zi.external_attr = info.external_attr
            if info.is_dir():
                zo.writestr(zi, b"")
                continue
            with zin.open(info) as fi, zo.open(zi, "w", force_zip64=True) as fo:
                prev = b""
                while True:
                    blk = fi.read(B)
                    buf = prev + blk
                    if not blk:
                        fo.write(prev)
                        break
                    new = reg.replace(buf, "text")[0]
                    keep = min(OV, len(new))
                    fo.write(new[:len(new) - keep])
                    prev = new[len(new) - keep:]


def stream_fallback(row, a, pm, reg, why):
    """Low-memory path for a huge file whose worker died: fetch to disk, then
    same-length replacement block by block (overlapping, so no match is cut)."""
    import tempfile
    path = row[0]
    dst = os.path.join(a.out, pm[path])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp_in = dst + ".in~"
    try:
        if a.local:
            src = os.path.join(a.local, path)
        else:
            from .discover import BASE
            import subprocess
            subprocess.run(["curl", "-fsS", "--retry", "5", "-o", tmp_in, f"{BASE}/{row[3]}"], check=True)
            src = tmp_in
        with open(src, "rb") as fh:
            magic = fh.read(4)
        if magic == b"PK\x03\x04":
            h = hashlib.md5()
            with open(src, "rb") as fh:
                for blk in iter(lambda: fh.read(1 << 24), b""):
                    h.update(blk)
            _zip_stream_rewrite(src, dst + ".part~", reg)
            os.replace(dst + ".part~", dst)
            return {"p": path, "np": pm[path], "n": int(row[1]), "m": os.path.getsize(dst), "chg": True,
                    "notes": [f"large-file-streamed:{why}"], "md5in": h.hexdigest()}
        B, OV = 64 << 20, 4096
        h = hashlib.md5()
        with open(src, "rb") as fi, open(dst + ".part~", "wb") as fo:
            prev = b""
            while True:
                blk = fi.read(B)
                h.update(blk)
                buf = prev + blk
                if not blk:
                    fo.write(prev)
                    break
                new = reg.replace(buf, "bin")[0]
                # keep the tail for the next round so matches across blocks are seen
                keep = min(OV, len(new)) if blk else 0
                fo.write(new[:len(new) - keep])
                prev = new[len(new) - keep:]
        os.replace(dst + ".part~", dst)
        return {"p": path, "np": pm[path], "n": int(row[1]), "m": os.path.getsize(dst), "chg": True,
                "notes": [f"large-file-streamed:{why}"], "md5in": h.hexdigest()}
    finally:
        if os.path.exists(tmp_in):
            os.unlink(tmp_in)


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
    big = [r for r in todo if int(r[1]) > 60_000_000]
    small = [r for r in todo if int(r[1]) <= 60_000_000]
    print(f"{len(todo)} to do ({len(big)} large), {len(done)} done", flush=True)
    t0 = time.time()
    nb = 0

    def record(log, res):
        nonlocal nb
        log.write(json.dumps(res, ensure_ascii=False) + "\n")
        nb += res.get("n", 0) or 0
        if "err" in res:
            return
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

    from concurrent.futures import ProcessPoolExecutor, as_completed
    from concurrent.futures.process import BrokenProcessPool
    with open(a.log, "a") as log:
        # small files: many workers.  A worker that dies (out of memory) breaks
        # the executor instead of hanging; the remaining files are retried.
        pending = list(small)
        while pending:
            retry = []
            with ProcessPoolExecutor(a.jobs, initializer=init, initargs=(a.registry, a.out, a.local, pm),
                                     max_tasks_per_child=100) as ex:
                futs = {ex.submit(work, r): r for r in pending}
                for i, f in enumerate(as_completed(futs)):
                    try:
                        res = f.result()
                        if res.get("err") == "memory":
                            big.append(futs[f])
                        else:
                            record(log, res)
                    except BrokenProcessPool:
                        retry.append(futs[f])
                    except Exception as e:
                        record(log, {"p": futs[f][0], "np": pm[futs[f][0]], "err": f"worker:{e}"[:200]})
                    if i % 1000 == 0:
                        log.flush()
                        dt = time.time() - t0
                        print(f"{i}/{len(todo)} {nb/1e9:.1f} GB {nb/1e6/max(dt,1):.1f} MB/s {dt/60:.1f} min", flush=True)
            if len(retry) == len(pending):
                for r in retry:
                    record(log, {"p": r[0], "np": pm[r[0]], "err": "worker crashed"})
                break
            pending = retry
        log.flush()
        # large files: two at a time, one task per worker process, each
        # capped at 4.5 GB; a capped/crashed one is streamed instead
        if big:
            with ProcessPoolExecutor(2, initializer=init, initargs=(a.registry, a.out, a.local, pm, 5.5),
                                     max_tasks_per_child=1) as ex:
                futs = {ex.submit(work, r): r for r in big}
                for f in as_completed(futs):
                    r = futs[f]
                    try:
                        res = f.result()
                        if res.get("err") == "memory":
                            res = stream_fallback(r, a, pm, reg, "memory")
                    except Exception as e:
                        res = None
                    if res is None:
                        res = stream_fallback(r, a, pm, reg, "worker-crash")
                    record(log, res)
                    log.flush()
                    print(f"large {r[0][-60:]} {int(r[1])/1e6:.0f} MB done {time.time()-t0:.0f}s", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
