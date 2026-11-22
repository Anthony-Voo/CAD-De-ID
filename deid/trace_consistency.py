"""Entity-resolution check: rewrite files and record, for every pseudonym
call, normalized original identity -> normalized pseudonym.

  python3 -m deid.trace_consistency --rows rows.tsv --input DIR --registry registry.json [--pdf-only]

Reports identities that became 2+ different pseudonyms (separators and case
ignored: 'D. Smith' -> 'X. Yyyyy' and 'dsmith' -> 'xyyyyy' are one
pseudonym) and pseudonyms produced for 2+ different identities."""
from __future__ import annotations

import argparse
import collections

from . import pseudo, registry as regmod, rewrite
from .pseudo import norm_key

K2P = collections.defaultdict(set)
P2K = collections.defaultdict(set)
EX = {}


def _rec(key, original, out):
    if isinstance(original, bytes):
        try:
            original = original.decode("utf-8")
            out = out.decode("utf-8")
        except UnicodeDecodeError:
            return
    ko, kp = norm_key(original), norm_key(out)
    if len(ko) < 3 or ko == kp:
        return
    K2P[ko].add(kp)
    P2K[kp].add(ko)
    EX.setdefault((ko, kp), (original, out))


def _wrap(mod, name):
    f = getattr(mod, name)

    def w(kind, key, original, *a):
        r = f(kind, key, original, *a)
        _rec(key, original, r)
        return r
    setattr(mod, name, w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--pdf-only", action="store_true")
    ap.add_argument("--show", type=int, default=25)
    a = ap.parse_args()
    reg = regmod.Registry.from_json(a.registry)
    # trace only the rewrite itself (registry loading tries salts on purpose)
    for mod in (pseudo, regmod, rewrite):
        for name in ("pseudo_entity", "pseudo_entity_bytes", "pseudo_text", "pseudo_bytes"):
            if hasattr(mod, name):
                _wrap(mod, name)
    n = 0
    for l in open(a.rows):
        p = l.split("\t")[0]
        if a.pdf_only and not p.lower().endswith(".pdf"):
            continue
        try:
            data = open(f"{a.input}/{p}", "rb").read()
        except OSError:
            continue
        n += 1
        try:
            rewrite.transform(p, data, reg)
        except Exception:
            pass
    multi = {k: v for k, v in K2P.items() if len(v) > 1}
    coll = {k: v for k, v in P2K.items() if len(v) > 1}
    print(f"files: {n}  identities pseudonymized: {len(K2P)}")
    print(f"IDENTITIES WITH 2+ PSEUDONYMS: {len(multi)}")
    for k, v in sorted(multi.items())[: a.show]:
        print("   ", k, "->", [EX[(k, x)] for x in sorted(v)][:4])
    print(f"PSEUDONYMS SHARED BY 2+ IDENTITIES: {len(coll)}")
    for k, v in sorted(coll.items())[: a.show]:
        print("   ", k, "<-", [EX[(x, k)] for x in sorted(v)][:4])


if __name__ == "__main__":
    main()
