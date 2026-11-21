#!/usr/bin/env python3
"""Write the attempt's README.txt from a finished run (uses only final, de-identified paths).

Usage: make_readme.py OUT [OUT ...] > README.txt   (several OUT dirs when the run was sharded)
"""
import collections
import json
import os
import sys

outs = sys.argv[1:]
defects = collections.defaultdict(list)
dups = 0
kinds = collections.Counter()
for out in outs:
    p = os.path.join(out, "private")
    for line in open(os.path.join(p, "defects.tsv"), encoding="utf-8"):
        newkey, what = line.rstrip("\n").split("\t", 1)
        if what.startswith("byte-identical duplicate"):
            dups += 1
        else:
            defects[what].append(newkey)
    for line in open(os.path.join(p, "log.tsv"), encoding="utf-8"):
        _, newkey, status, stats = line.rstrip("\n").split("\t", 3)
        st = json.loads(stats)
        kinds[status] += 1
        for k in st:
            if k.startswith(("sw2015_kept", "container_error", "error:")):
                kinds[k] += 1
                if k == "sw2015_kept":
                    defects["SolidWorks 2015+ file kept byte-identical (renamed only; see limitations)"].append(newkey)
                elif k.startswith("container_error"):
                    defects[f"archive/container could not be opened ({k.split(':', 1)[1]}); member names scrubbed in place, data untouched"].append(newkey)

print("""De-identified engineering corpus: attempt README
===================================================

HOW TO RUN
----------
Python 3.11+. Install the pinned dependencies, then run:

    pip install -r requirements.txt
    export DEID_KEY=<secret>              # pseudonym key, kept private, not in this submission
    python3 run.py --manifest manifest.tsv --out OUT --workers 16 [--shard I/N]
    python3 tools/make_readme.py OUT > README.txt

manifest.tsv is the organizer's file list (path, size, etag). run.py reads each
file from the public corpus bucket over HTTPS (or --input DIR for a local copy)
and writes OUT/corpus/. OUT/private/ (identity list, old->new mapping, logs) is
never uploaded. tools/validate.py and tools/validate_run.py re-open every output
with real parsers (zip CRC test, pikepdf, olefile, PIL, gzip, STEP structure).

WHAT IT DOES
------------
* One pseudonym per identity, everywhere: pseudonyms come from a keyed hash of
  the identity, so the same name gets the same pseudonym in every file, path and
  archive, and different names stay distinct. Each pseudonym keeps a meaningful
  class prefix where it fits (Person..., Org..., Proj..., user...) and has exactly
  the byte length of the original, so offsets inside binary formats never move.
* Identities: GitHub/GitLab owners and distinctive project names, web domains,
  institution/person/project names in catalog titles (reviewed by hand), e-mail
  addresses, Windows/Unix user-profile folders inside stored paths (ASCII and
  UTF-16), STEP FILE_NAME author/organization/authorization, PDF Info and XMP
  authors.
* Paths: identifying folder and file-name parts are replaced; extensions, snapshot
  hashes and every non-identifying part are kept. Because names inside files are
  replaced with the same pseudonyms, references between assemblies, parts and
  drawings keep pointing at the renamed files.
* Containers are opened and re-packed with the same compression, recursively:
  ZIP family (.zip, .fcstd, .3mf, Office files, ...), gzip and 7z. No unprocessed
  original copy is left inside any output archive.
* Geometry, units, tolerances, materials, configurations and BOM data are never
  edited; only identifying strings are, at the same length.
* Duplicates are kept (each at its own renamed path), never dropped.

LIMITATIONS AND EXCEPTIONS
--------------------------""")
print(f"* Files processed: {sum(v for k, v in kinds.items() if k in ('ok', 'fallback'))}; "
      f"duplicates copied: {kinds.get('duplicate_copy', 0)}; "
      f"handler fallbacks (generic same-length scrub): {kinds.get('fallback', 0)}.")
print("""* SolidWorks 2015+ native files store their properties in compressed,
  checksummed sections. This version keeps them byte-identical (renamed only) so
  they stay openable; their internal author/path properties are not rewritten.
* Visible text inside images and in vector drawing geometry, and identifying text
  rendered only as graphics in PDFs, is not OCR'd in this version.
* Common words that happen to be project or owner names are only replaced in
  paths, not inside file content, to avoid corrupting format keywords.

SOURCE DEFECTS (final paths)
----------------------------""")
print(f"* {dups} files are byte-identical duplicates of another file; all are kept.")
for what, keys in sorted(defects.items(), key=lambda kv: -len(kv[1])):
    print(f"\n{what}: {len(keys)} file(s)")
    for k in sorted(keys)[:400]:
        print(f"  {k}")
    if len(keys) > 400:
        print(f"  ... and {len(keys) - 400} more")
