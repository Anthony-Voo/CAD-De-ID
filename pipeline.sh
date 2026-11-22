#!/usr/bin/env bash
# Full pipeline: (discovery ->) identity list -> rewrite -> README.txt.
# Usage: bash pipeline.sh WORKDIR
#   The identity list is rebuilt from the input by the discovery pass
#   (an existing WORKDIR/registry.json is reused if present).
set -euo pipefail
W="${1:?work dir}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE"
export DEID_SECRET="${DEID_SECRET:-machina-deid-v1}"   # pseudonym key (default = the one used for this submission)
mkdir -p "$W"
curl -fsS -o "$W/manifest.tsv" https://machina-input.joinbuilders.org/manifest.tsv
if [ -f "$W/registry.json" ] && [ -z "${REDISCOVER:-}" ]; then
  echo "reusing $W/registry.json"
else
  python3 -m deid.discover --manifest "$W/manifest.tsv" --out "$W/discovered.jsonl" --jobs 12
  python3 -m deid.build_registry --manifest "$W/manifest.tsv" --discovered "$W/discovered.jsonl" \
      --out "$W/registry.json" --report "$W/discovery-report.tsv"
fi
python3 -m deid.run --manifest "$W/manifest.tsv" --registry "$W/registry.json" --out "$W/cleaned-corpus" \
    --log "$W/run.jsonl" --pathmap-out "$W/pathmap.tsv" --jobs 8
python3 -m deid.readme --log "$W/run.jsonl" --out "$W/README.txt"
