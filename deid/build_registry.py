"""Merge seed identities and discovered values into one registry.

  python3 -m deid.build_registry --manifest manifest.tsv --discovered discovered.jsonl --out registry.json
"""
from __future__ import annotations

import argparse
import collections
import json
import re

from .harvest import NS_DOMAINS, clean, is_placeholder
from .pseudo import norm_key
from .registry import Registry
from .seeds import CURATED, SOFTWARE, add_domain, build_seed_registry

try:
    from wordfreq import zipf_frequency
except ImportError:  # pragma: no cover
    zipf_frequency = None

LANGS = ("en", "fr", "de", "es", "it", "pt", "nl")
COMMON = 3.3  # zipf >= 3.3: an everyday word in some language


def commonness(word: str) -> float:
    if zipf_frequency is None:
        return 0.0
    w = word.lower()
    return max(zipf_frequency(w, l) for l in LANGS)


def is_common_single(v: str) -> bool:
    """Every token is an everyday word (single words and generic phrases)."""
    toks = [t for t in re.split(r"[\s_\-.,&()]+", v) if t and not t.isdigit()]
    if not toks or len(toks) > 4:
        return False
    return all(commonness(t) >= COMMON for t in toks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--discovered", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--report")
    a = ap.parse_args()
    paths = [l.split("\t")[0] for l in open(a.manifest) if not l.startswith("#")]
    reg = build_seed_registry(paths)
    curated_keys = {norm_key(t) for _, t, _ in CURATED}

    counts = collections.Counter()
    files = collections.defaultdict(set)
    for fn in a.discovered:
        for line in open(fn):
            try:
                d = json.loads(line)
            except Exception:
                continue
            for k, v in d.get("h") or []:
                counts[(k, v)] += 1
                if len(files[(k, v)]) < 3:
                    files[(k, v)].add(d["p"])

    for (k, v), n in counts.items():
        v = clean(v)
        if not v or is_placeholder(v):
            continue
        if k == "email":
            local, dom = v.split("@", 1)
            reg.add("email", v, substr=True)
            if not NS_DOMAINS.search(dom):
                add_domain(reg, dom)
            if len(norm_key(local)) >= 3:
                reg.add("account", local, ctx=("path", "text", "bin") if len(local) >= 5 else ("path", "text"))
            continue
        if k == "domain":
            if not NS_DOMAINS.search(v):
                add_domain(reg, v)
            continue
        if k == "host":
            reg.add("host", v, ctx=("path", "text", "bin"))
            continue
        if "\\" in v and k in ("person", "account"):
            from .harvest import person_values
            for k2, v2 in person_values(v):
                if len(norm_key(v2)) >= 3:
                    reg.add(k2, v2, ctx=("path", "text", "bin") if len(v2) >= 5 else ("path", "text"))
            continue
        nk = norm_key(v)
        if len(nk) < 3:
            continue
        ctx = ("path", "text", "bin") if len(v) >= 5 else ("path", "text")
        reg.add(k, v, ctx=ctx)

    # never treat a file-format keyword or extension used in the corpus as an identity
    from .formats import ext_of
    exts = {norm_key(ext_of(p)) for p in paths}
    exts |= {"kicadpcb", "kicadsch", "kicadmod", "kicadpro", "sldprt", "sldasm", "slddrw", "fcstd", "step", "iges"}
    for (kind, key) in list(reg.ids):
        if key in exts and kind in ("domain", "account", "project", "person", "org"):
            reg.ids.pop((kind, key))
    # demote everyday words: they are only replaced inside known identity
    # fields (handled per file), never as free text
    demoted = []
    for (kind, key), ident in list(reg.ids.items()):
        if key in curated_keys and kind in ("org", "person", "project", "code"):
            continue
        if kind in ("email",):
            continue
        vs = [v for v in ident.variants]
        if key in SOFTWARE or all(is_common_single(v) for v in vs):
            if "path" in ident.ctx and kind in ("account", "project") and key not in SOFTWARE:
                # owner/repo position is handled structurally; keep path ctx only
                ident.ctx = set()
            else:
                ident.ctx = set()
            demoted.append(key)
    reg._auto = {}
    reg.to_json(a.out)
    print(f"{len(reg.ids)} identities, {len(demoted)} common words demoted to field-only")
    if a.report:
        with open(a.report, "w") as f:
            for (k, v), n in counts.most_common():
                f.write(f"{n}\t{k}\t{v}\t{' | '.join(sorted(files[(k, v)]))}\n")


if __name__ == "__main__":
    main()
