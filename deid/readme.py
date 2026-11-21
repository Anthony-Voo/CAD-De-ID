"""Write README.txt for an attempt from the run log (final paths only)."""
from __future__ import annotations

import argparse
import collections
import json

from .formats import ext_of

HEAD = """MACHINA CAD DE-ID - SUBMISSION NOTES
=====================================

corpus/    de-identified files, same folder layout as the input, every file kept
code/      complete pipeline source (deid/), requirements.txt, pipeline.sh
README.txt this file

HOW TO RUN (reproduces this corpus exactly)
-------------------------------------------
  python3.13 -m venv .venv && . .venv/bin/activate
  python3 -m pip install -r code/requirements.lock        # exact versions used
  bash code/pipeline.sh ./work                            # -> ./work/cleaned-corpus

  The pipeline is deterministic: pseudonyms are an HMAC of the normalized
  identity under the key DEID_SECRET.  This attempt used the default key in
  code/deid/pseudo.py, so the command above regenerates byte-identical files
  (set DEID_SECRET to a private value for a fresh, unlinkable mapping).
  pipeline.sh downloads the input from the Builders mirror, so it needs no
  local copy; on a machine with less than ~150 GB free use
  code/tools/drive2.sh, which processes, uploads and deletes ~30 GB chunks.
  Private artefacts (registry.json identity list, pathmap.tsv, run logs) are
  written to the work directory only and are not part of this submission.

Steps (all in code/deid/):
  1. discover.py       streams every input file and harvests identity values:
                       STEP FILE_NAME author/organization, IGES global author/org,
                       Parasolid SITE/USER, DXF $LASTSAVEDBY, PDF Info + XMP +
                       attachments, OLE property sets (SolidWorks <2015,
                       Inventor, Solid Edge), SolidWorks 2015+ docProps XML,
                       FreeCAD/3MF/Fusion XML, e-mails, URL domains, user
                       folders in embedded paths (C:\\Users\\<name>), DOMAIN\\login.
  2. build_registry.py merges those values with identities from the corpus
                       layout (GitHub/GitLab owners and repositories, web
                       domains, catalog titles) and a curated list of
                       organizations, people, project names and project codes.
  3. run.py/rewrite.py rewrites paths and contents (below).

PSEUDONYMS
----------
* One identity -> one pseudonym everywhere (paths, text, metadata, binary
  streams, archive members).  Pseudonyms are derived from a keyed hash of the
  normalized identity, so "Ann Example", "Ann_Example", "ANN EXAMPLE" and
  "annexample" all become the same pronounceable fake name with the same
  separators and capitalization.  Different identities get different names.
* A pseudonym has exactly the length of the original (letters->letters,
  digits->digits, punctuation kept).  This is what keeps binary CAD files
  valid: no offset, record length, fixed IGES column or stream size moves.
* Identity fields are never blanked: author/organization/company/designer
  fields keep a (pseudonymous) value.
* Everyday words used as an author name are pseudonymized inside the
  identity field where they were found, but not replaced as free text
  elsewhere, so engineering words, materials and units stay intact.

WHAT IS REWRITTEN
-----------------
* File and folder names: owner__repository folders, web domains, and every
  identifying token in deeper folder and file names.  Extensions and the
  non-identifying parts of names are unchanged.  References inside files
  are rewritten with the same mapping, so assemblies, drawings and
  relative links still point to the renamed files.
* Text formats (STEP, IGES, DXF, SVG, OBJ, VRML, Parasolid text, ACIS, UNV,
  PostScript, scripts, ...): same-length replacement; STEP FILE_NAME
  author/organization always pseudonymized.
* OLE compound files (SolidWorks <2015, Inventor .ipt/.iam/.idw/.ipn,
  Solid Edge .par/.asm/.dft/.psm): property sets are parsed and identity
  fields rewritten in place; other streams get same-length replacement.
  Stream sizes and the OLE structure are unchanged.
* SolidWorks 2015+ (.sldprt/.sldasm/.slddrw/...): every internal
  deflate stream (docProps/core.xml, custom.xml, swXmlContents, references,
  ...) is decompressed, rewritten, recompressed to exactly its original
  size and its CRC32 updated, so the container layout is unchanged.
* PDF: Info dictionary, XMP metadata, every string object, page content
  streams (visible text, including text split by kerning), annotations and
  links, and embedded attachments (recursively).  Fonts and images are
  untouched.
* Zip-based formats (FreeCAD .FCStd, .3mf, Fusion .f3d/.f3z, .odg, .zip):
  every member rewritten and renamed, then repacked with the same
  compression method.  tar, tar.gz/.tgz, .gz, .bz2, .xz, .Z (LZW) and .7z
  archives are unpacked, rewritten recursively and repacked in the same
  format, so no unprocessed copy of an original identifier remains.
* Phone/fax numbers next to a Tel/Phone/Fax/Mobile label get same-format
  fake numbers (digits replaced, punctuation kept).
* PNG text chunks and JPEG APP/comment segments (EXIF/XMP).
* Other binary formats (JT, Parasolid binary, CATIA V5, NX, Creo, DWG, U3D,
  STL headers, ...): same-length replacement of distinctive identifiers
  (5+ characters) in the raw bytes.

LIMITATIONS
-----------
* Pixels of raster images and logos inside images are not edited.
* DWG R2004+ data sections are compressed by AutoCAD; strings inside them
  are not visible to the byte-level pass.
* Proprietary CATIA/NX/Creo/JT internals that are compressed are only
  covered where identifiers appear uncompressed.
* Very short identifiers (under 5 characters) are not replaced inside raw
  binary data, to avoid corrupting geometry; they are replaced in text,
  metadata fields and names.
"""


KNOWN = {
    "7z-rewrite-failed", "zip-rewrite-failed", "zip-open-failed", "gz-decode-failed", "Z-decode-failed",
    "Z-recompress-failed", "rar-kept-binary-rewrite", "pdf-open-failed", "pdf-rewrite-failed", "ole-open-failed",
    "ole-write-failed", "tar-open-failed", "defect-lockfile", "defect-vti", "defect-partial-download",
    "defect-step-not-iso", "defect-step-is-zip", "defect-pdf-not-pdf", "bz2-decode-failed", "xz-decode-failed",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.log)]
    by_ext = collections.Counter()
    changed = collections.Counter()
    empty = []
    defects = collections.defaultdict(list)
    md5s = collections.defaultdict(list)
    errors = []
    for d in rows:
        if "err" in d:
            errors.append(d["np"])
            continue
        e = ext_of(d["np"]) or "(none)"
        by_ext[e] += 1
        if d.get("chg"):
            changed[e] += 1
        if d.get("n", 0) == 0:
            empty.append(d["np"])
        md5s[d.get("md5in")].append(d["np"])
        for n in d.get("notes", []):
            if n == "duplicate":
                continue
            # notes of archive members start with the member's ORIGINAL name:
            # never print that, keep only the known note code
            codes = [c for c in n.split(":") if c in KNOWN or c.startswith(("sw3d-streams-kept", "large-file-streamed"))]
            if not codes:
                continue
            defects[codes[0] if not codes[0].startswith("sw3d") else "sw3d-streams-kept"].append(d["np"])
            continue
            tag = n.split(":")[-1] if ":" in n and not n.startswith(("7z", "zip", "gz", "Z", "pdf", "ole", "tar", "rar", "sw3d", "error", "fatal")) else n
            key = n.split(":")[0]
            if "!" in n.split(":")[0] or "#" in n.split(":")[0]:
                key = n.split(":")[1] if len(n.split(":")) > 1 else key
            defects[key].append(d["np"])
    with open(a.out, "w") as f:
        f.write(HEAD)
        f.write("\nCORPUS SUMMARY\n--------------\n")
        f.write(f"files written: {sum(by_ext.values())} (input: {len(rows)})\n")
        f.write(f"files whose bytes changed: {sum(changed.values())}\n")
        f.write("by extension (files / changed):\n")
        for e, n in by_ext.most_common(60):
            f.write(f"  {e:12} {n:7} {changed[e]:7}\n")
        dups = [v for k, v in md5s.items() if k and len(v) > 1]
        f.write(f"\nDUPLICATES\n----------\n{len(dups)} groups of byte-identical input files "
                f"({sum(len(v) for v in dups)} files).  All copies are kept and rewritten identically.\n")
        f.write("\nSOURCE DEFECTS AND EXCEPTIONS\n-----------------------------\n")
        f.write(f"Empty (0-byte) input files, kept as empty files ({len(empty)}):\n")
        for p in empty:
            f.write(f"  {p}\n")
        names = {
            "7z-rewrite-failed": "7z archive that cannot be opened (password-protected or truncated in the source); kept, raw bytes scanned",
            "zip-rewrite-failed": "zip using an unsupported compression method or damaged; kept, raw bytes scanned",
            "zip-open-failed": "file with a .zip-like signature that is not a valid zip in the source; kept, raw bytes scanned",
            "gz-decode-failed": "truncated/invalid gzip in the source; kept unchanged",
            "Z-decode-failed": "invalid .Z in the source; kept unchanged",
            "Z-recompress-failed": ".Z archive that could not be recompressed; kept unchanged",
            "rar-kept-binary-rewrite": "RAR archive: no RAR writer is available, so the archive is kept as RAR (raw bytes scanned, compressed members not rewritten)",
            "pdf-open-failed": "PDF that cannot be parsed (damaged in the source); raw bytes scanned",
            "pdf-rewrite-failed": "PDF that could not be re-saved; raw bytes scanned",
            "ole-open-failed": "OLE file that cannot be parsed (damaged in the source); raw bytes scanned",
            "ole-write-failed": "OLE file whose streams could not be written back; kept unchanged",
            "tar-open-failed": "invalid tar in the source; raw bytes scanned",
            "defect-lockfile": "CAD/Office lock file (~$ prefix) in the source, not an engineering file; kept",
            "defect-vti": "FrontPage _vti_cnf/_vti_pvt metadata stub in the source; kept",
            "defect-partial-download": "truncated partial download (.resume-partial) in the source; kept",
            "defect-step-not-iso": ".step/.stp file without an ISO-10303-21 header in the source; text-scrubbed and kept",
            "defect-step-is-zip": ".step/.stp file that is actually a ZIP archive in the source; processed as zip and kept",
            "defect-pdf-not-pdf": ".pdf file that is not a PDF in the source; raw bytes scanned and kept",
        }
        for key, paths in sorted(defects.items()):
            label = names.get(key) or names.get(key.split(":")[0])
            if key.startswith("large-file-streamed"):
                label = "very large file processed in streaming mode (same-length replacement only)"
            if label is None and not key.startswith("sw3d"):
                continue
            if key.startswith("sw3d-streams-kept"):
                label = "SolidWorks file where some internal streams could not be recompressed to their original size and were kept"
            f.write(f"\n{label} ({len(paths)}):\n")
            for p in sorted(set(paths))[:400]:
                f.write(f"  {p}\n")
        if errors:
            f.write(f"\nFiles that could not be fetched ({len(errors)}):\n")
            for p in errors:
                f.write(f"  {p}\n")


if __name__ == "__main__":
    main()
