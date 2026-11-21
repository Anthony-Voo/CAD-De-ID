# CAD De-ID handoff (Machina track, Builders Buildathon)

Status as of Sat Nov 21, ~00:30 UTC. **Nothing has been submitted yet. 0 of 20 attempts used.**
Cutoff: **Sun Nov 22, 11:00 AM ET (16:00 UTC)**. At most 2 submissions an hour.

## The task in one paragraph
De-identify a 139.7 GB corpus (188,722 files: STEP/SAT/STL/PDF/SolidWorks/Inventor/Creo/FreeCAD/images/archives).
- Replace identifying people, companies, accounts, emails, domains, addresses, logos, and project or part names with **consistent pseudonyms**: one identity gets one pseudonym everywhere, and different identities stay different.
- Cover file and folder names, file content, PDF metadata, native CAD properties, embedded previews, and archive contents.
- Keep every file usable: geometry, units, BOMs and cross-file references must survive.
- Explain source defects in `README.txt`.

Scoring: 1,000 private checks, with only per-category counts as feedback. Copying the input unchanged scores **292** (the baseline). No team had a score on the board when last checked.
- The scorer **finds each file by its content and its path**. Keep extensions and the non-identifying parts of paths.
- A check on a file the scorer can't find or open counts as a fail.

## Submission mechanics
1. Track page → **Get upload token**. It lasts 1 hour and the user has to press it; it's not automatable. The panel shows the destination `gs://builders-machina-submissions-1120/teams/<team-id>/attempt-N/`.
2. `python3 upload.py --dest <that>/ --corpus OUT/corpus --code <pipeline source> --readme README.txt`. upload.py comes from the track page.
   - It **never deletes** and skips files it has already uploaded, so it can run in batches. Upload a shard, delete it locally, repeat.
   - `--token-file F` waits for a fresh token written to F.
3. The user submits the `attempt-N/` path on the track page. Every attempt needs the **full** corpus, code and README again.

## Where things are
- **Repo:** `Anthony-Voo/CAD-De-ID`. `main` holds only a placeholder README. The pipeline is on branch **`handoff/pipeline-v1`**.
  - The user's rule: push to `main` only a version that has been **fully verified end to end** as an acceptable submission.
- **Corpus access:**
  - The bucket is publicly readable over HTTPS with no AWS account: `https://machina-deid-v1-challenge.s3.us-west-2.amazonaws.com/releases/v1/corpus/<key>`.
  - ListObjectsV2 works too. `tools/`-style lister: see `run.py` `S3` const.
  - That's about 21 MB/s per stream, so use many parallel streams.
  - The event's own mirror, `machina-input.joinbuilders.org`, was **blocked by the Claude cloud network proxy**. Use S3 instead.
- **Manifest:** build `manifest.tsv` (key, size, etag) by listing the bucket. That takes about 1 minute and gives 188,722 files and 139.7 GB.
- **Disk:** a cloud container had about 60 GB free, and the output is about the same size as the input. So process in shards: `--shard I/N` groups each repo folder together.

## Pipeline v1 (branch `handoff/pipeline-v1`)
| File | Role |
|---|---|
| `deid/pseudo.py` | Deterministic pseudonyms from an HMAC of the case-folded identity, using the secret key `DEID_KEY`. Each one has **exactly the original's byte length**, a class prefix where it fits (`Person…`, `Org…`, `Proj…`, `user…`), and the original's letter case. |
| `deid/identities.py` | Identity harvest from manifest paths: GitHub/GitLab owners, distinctive repo names, web domains. Aho-Corasick whole-word, same-length scrubber over ASCII and UTF-16LE. Rule detectors for emails (real TLDs only) and user-profile folders in stored paths (`C:\Users\x`, `/home/x`, ASCII and UTF-16). Content filters: English dictionary, software and format vocabulary, minimum length. |
| `deid/curated.py` | Hand-reviewed identities from the 64 `sources/catalog/<title>` folders: institutions, 3 people, named projects, and 13 site domains. Short or common ones are path-only. |
| `deid/paths.py` | Structural renaming: the owner and project in `sources/github/<owner>__<repo>[__hash]`, gitlab, `sources/web/<domain>`, and identifying tokens inside catalog titles. The rest of the path goes through the same scrubber. |
| `deid/process.py` | Routing by magic bytes, then by extension. Zip family (`.zip`, `.fcstd`, `.3mf`, Office), gzip and 7z are unpacked, processed recursively and repacked. PNG text chunks are scrubbed and their CRCs recomputed. STEP `FILE_NAME` author, org and authorization are rewritten at the same length. PDF `/Author` and `/Creator` (non-software) and XMP creator/author are rewritten at the same length. Everything else gets the generic same-length scrub. |
| `run.py` | Sharded runner: download, process, write `OUT/corpus/<renamed key>`. Processes each duplicate group (same etag) once and copies the result. Never drops a file; on a handler error it falls back to the generic scrub. Writes `OUT/private/{mapping,log,defects}.tsv`, which must **never be uploaded**. |
| `tools/validate.py`, `tools/validate_run.py` | Reopen every output with real parsers: zip CRC test, pikepdf page count, olefile stream count, PIL, gzip, STEP statement count, and same size for everything else. |
| `tools/make_readme.py` | Writes the attempt's README.txt using final, de-identified paths only. |
| `tools/run_shards.sh` | Runs shards I..N with one key. It hard-codes some paths; adjust them. |

Run: `export DEID_KEY=<secret, keep it out of the repo and constant across ALL shards and attempts>; python3 run.py --manifest manifest.tsv --out OUT --shard 0/8 --workers 16`. Dependencies are in `requirements.txt`.

**Verified:** 1,500 random corpus files, with 0 files broken by processing. 626 were changed and the rest are byte-identical. The only flag was an `.f3d` zip using a compression method Python doesn't support; the original fails to open the same way.

**Throughput:** about 1,500 files (1.4 GB) in 2 minutes on 4 cores. Shard 0 of 8 had reached 14.4k files and 17 GB when stopped; it's local to the old container and can be discarded.

## Pitfalls already learned (don't repeat them)
- Path-harvested "identities" include **format keywords and software names**. Replacing them in content corrupts files. Real examples: repos named `Resources` (a required PDF key) and `SolidWorks`/`FreeCAD`. That's why content scrubbing has the dictionary and software filters. Keep them.
- `name@file.SLDPRT` looks like an email but is an internal SolidWorks key, so emails need a real TLD.
- XMP namespace URIs (for example `www.aiim.org`) are format-required. **Never** do blanket URL-host replacement.
- Raw scrubbing of zip-family files breaks CRCs. Always unpack. For unreadable zips, the generic scrub of plain-text member names is acceptable.
- 1,000 checks plus "file must be findable": don't rename non-identifying path parts or change extensions.

## Gaps, highest value first (v1 does NOT do these)
1. **SolidWorks 2015+ native files** (about 84% of SolidWorks files, roughly 19k files, about 12% of all files) are kept **byte-identical**.
   - The container has nibble-swapped section names and raw-deflate sections, with a CRC-32 of the decompressed data in each section header.
   - Identifiers live in the `docProps` core/custom XML, `ISolidWorksInformation`, `Biography` (save history with user paths), `PreviewPNG`, and reference paths in Header2/CMgr/COMPINSTANCETREE.
   - The fix: decompress → scrub (same length) → recompress → update size and CRC fields.
   - The survey had a working decoder, `swnew.py`. Section marker `14 00 06 00 08 00`; see `goonies-buildathon/tracks/machina-deid/survey/tools/swnew.py` on branch `claude/bold-newton-tcic14`.
2. **Person names in folder and file names**, for example `.../<First>_<Last>/...` or a surname in a thesis-data folder. Not caught. A first-name list plus a capitalized surname that isn't a dictionary word would catch most.
3. **More identity harvesting (a first pass over all headers).** Collect STEP `FILE_NAME` authors and orgs (about 51% of STEP headers have a real organization and about 16% a real author), PDF authors (about 60% are `DOMAIN\user (MACHINE)`), and Inventor/OLE property authors. Then scrub those strings corpus-wide for consistency. Today they're replaced only in their own field.
4. Company and product names inside filenames that aren't in any list, for example a 3D-printer maker's model prefix or a university acronym in a folder name.
5. Inventor OLE property sets: the `\x05…` streams are obfuscated, olefile misparses them, and the Summary set embeds a PNG thumbnail. UTF-16 user paths in `UFRxDoc` are already caught by the user-folder rule.
6. PDF visible text (title blocks with names, emails, addresses) and raster images (drawn title blocks, EXIF). Needs OCR, so it's low priority.
7. STEP `\X2\…\X0\` encoded strings, and `PRODUCT` names and descriptions.

## Source defects to report in README.txt (already detected by run.py)
24 zero-byte files, about 281 `~$` lock files, 279 FrontPage `_vti_cnf` stubs, 2 `.resume-partial` truncated downloads, a `.step` that is actually a ZIP, a `.step` with no ISO header, 35,656 duplicate groups (40,889 extra copies, 33.5 GB), and 695 archives next to an unpacked `X.extracted/` twin, which must get identical pseudonym paths. That last one works automatically because pseudonyms are deterministic.

## Suggested next steps
1. Clone the branch, regenerate the manifest from S3, generate a **new** `DEID_KEY` (nothing has been submitted yet), and run the sample check:
   - `run.py --sample 1500`
   - `tools/validate_run.py OUT`
2. Get attempt 1 on the board early, since scores are per-category, and the feedback tells you which gaps matter. That needs the user for the hourly token.
3. Then add SolidWorks 2015+ handling (gap 1) and the header-harvest pass (gap 3).
