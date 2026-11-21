#!/usr/bin/env bash
# Build and upload one attempt in disk-sized chunks.
#   bash attempt.sh WORKDIR ATTEMPT_N TOKEN_FILE [CHUNK_GB]
# WORKDIR must hold manifest.tsv and registry.json (see pipeline.sh).
# TOKEN_FILE holds the current upload token from the track page; upload.py
# waits for a fresh token there whenever the current one expires.
set -euo pipefail
W="$1"; N="$2"; TOK="$3"; CHUNK="${4:-45}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE"
DEST="gs://builders-machina-submissions-1120/teams/Goonies/attempt-$N/"
OUT="$W/chunk"
LOG="$W/run-attempt-$N.jsonl"
UP="$W/upload.py"
TOTAL=$(grep -vc '^#' "$W/manifest.tsv")
while :; do
  DONE=$( [ -f "$LOG" ] && python3 -c "import json,sys;print(len({json.loads(l)['p'] for l in open(sys.argv[1]) if 'err' not in l}))" "$LOG" || echo 0)
  echo "== $DONE / $TOTAL files done"
  [ "$DONE" -ge "$TOTAL" ] && break
  rm -rf "$OUT"
  python3 -m deid.run --manifest "$W/manifest.tsv" --registry "$W/registry.json" --out "$OUT" \
      --log "$LOG" --jobs 10 --budget-gb "$CHUNK"
  MACHINA_UPLOAD_TOKEN="$(cat "$TOK")" python3 "$UP" --dest "$DEST" --corpus "$OUT" --token-file "$TOK"
  rm -rf "$OUT"
done
# code/ and README.txt last
rm -rf "$W/code-$N"; mkdir -p "$W/code-$N"
cp -r "$HERE/deid" "$HERE/requirements.txt" "$HERE/pipeline.sh" "$HERE/attempt.sh" "$W/code-$N/"
find "$W/code-$N" -name '__pycache__' -prune -exec rm -rf {} +
python3 -m deid.readme --log "$LOG" --out "$W/README-$N.txt"
MACHINA_UPLOAD_TOKEN="$(cat "$TOK")" python3 "$UP" --dest "$DEST" --code "$W/code-$N" --readme "$W/README-$N.txt" --token-file "$TOK"
echo "attempt $N uploaded to $DEST"
