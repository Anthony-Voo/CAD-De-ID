# CAD De-ID pipeline

De-identifies the Machina engineering corpus (188,722 files, 139.7 GB of
PDFs, drawings, native CAD and archives) with consistent pseudonyms while
keeping every file usable. This repository is what each attempt uploads as
its `code/` folder.

## Run

```bash
python3 -m pip install -r requirements.txt        # Python 3.13
DEID_SECRET=<private key> bash pipeline.sh ./work  # discover -> registry -> rewrite -> README.txt
```

To build and upload an attempt in disk-sized chunks (the output is larger
than most local disks):

```bash
bash attempt.sh ./work <N> ./work/token.txt 45
```

`token.txt` holds the upload token from the track page. When a token
expires, `upload.py` waits for a new one in that file.

## How it works

1. **Discovery** (`deid/discover.py`, `deid/harvest.py`) streams every file
   and harvests identity values. Sources: STEP and IGES headers, Parasolid
   `SITE`/`USER`, DXF `$LASTSAVEDBY`, PDF Info, XMP, page title blocks and
   attachments, OLE property sets (SolidWorks before 2015, Inventor, Solid
   Edge), SolidWorks 2015+ docProps and custom properties, FreeCAD, 3MF and
   Fusion XML, emails, URL domains, `C:\Users\<name>` paths and
   `DOMAIN\login` values.
2. **Registry** (`deid/build_registry.py`, `deid/seeds.py`) merges the
   harvested values with identities taken from the corpus layout (GitHub and
   GitLab owners and repositories, web domains, catalog titles) and a
   curated list. Everyday words are demoted to field-only replacement.
3. **Rewrite** (`deid/run.py`, `deid/rewrite.py`) maps every path and
   rewrites contents. Every pseudonym has exactly the original's length
   (`deid/pseudo.py`), so binary formats are edited in place:
   * text CAD formats: same-length replacement, plus the STEP author and
     organization fields;
   * OLE files: property values rewritten in place (`deid/oleprops.py`);
   * SolidWorks 2015+: each deflate stream is rewritten, then recompressed
     to its exact original size with the CRC updated;
   * PDF: metadata, all string objects, attachments, and visible text
     through each font's ToUnicode map (`deid/pdftext.py`);
   * zip, tar, gz, bz2, xz, .Z and 7z: unpacked, rewritten recursively, and
     repacked in the same format;
   * PNG/JPEG metadata, and distinctive identifiers inside other binaries.
4. **Validate** (`deid/validate.py`) checks that every output still opens
   with the same structure, and looks for leftover identifiers.
5. **README.txt** (`deid/readme.py`) lists exceptions, source defects and
   duplicates using final paths.
