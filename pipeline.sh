#!/usr/bin/env bash
# Full pipeline: discovery -> registry -> rewrite -> validate.
# Usage: bash pipeline.sh WORKDIR   (needs ~70 GB free with --local, or streams from the mirror)
set -euo pipefail
W="${1:?work dir}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE"
export DEID_SECRET="${DEID_SECRET:-machina-deid-v1}"   # pseudonym key; keep private to make the mapping irreversible
mkdir -p "$W"
curl -fsS -o "$W/manifest.tsv" https://machina-input.joinbuilders.org/manifest.tsv
python3 -m deid.discover --manifest "$W/manifest.tsv" --out "$W/discovered.jsonl" --jobs 12
python3 -m deid.build_registry --manifest "$W/manifest.tsv" --discovered "$W/discovered.jsonl" \
    --out "$W/registry.json" --report "$W/discovery-report.tsv"
python3 -m deid.run --manifest "$W/manifest.tsv" --registry "$W/registry.json" --out "$W/cleaned-corpus" \
    --log "$W/run.jsonl" --pathmap-out "$W/pathmap.tsv" --jobs 12
python3 -m deid.readme --log "$W/run.jsonl" --out "$W/README.txt"
