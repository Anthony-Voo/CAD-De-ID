#!/usr/bin/env bash
# Driver for attempt 1: finish the current chunk, then process/upload/delete
# the remaining corpus in chunks.  Token lives in secret/token.txt (never printed).
set -uo pipefail
W=/home/user/work
cd /home/user/cad-de-id
export PYTHONPATH=/home/user/cad-de-id
DEST=$(cat $W/dest2.txt)
LOG=$W/run-attempt-2.jsonl
up() {  # upload a directory as corpus/, then delete it
  local d="$1"
  find "$d" -name '*.part~' -delete
  until MACHINA_UPLOAD_TOKEN="$(cat $W/secret/token.txt)" python3 $W/upload.py --dest "$DEST" --corpus "$d" \
        --token-file $W/secret/token.txt --workers 32 >> $W/upload.log 2>&1; do
    echo "$(date -u +%H:%M) upload of $d stopped (token?); waiting for a new token" >> $W/drive2.log
    old=$(md5sum < $W/secret/token.txt)
    until [ "$(md5sum < $W/secret/token.txt)" != "$old" ]; do sleep 15; done
  done
  echo "$(date -u +%H:%M) uploaded $d" >> $W/drive2.log
  rm -rf "$d"
}
# resume: continue in the highest existing chunk folder (run.py skips files
# already in the log; partial *.part~ files are removed before upload)
k=2
for d in $W/a2chunk[0-9]*; do [ -d "$d" ] && n=${d##*chunk} && [ "$n" -gt "$k" ] && k=$n; done
[ -d $W/a2chunk ] && up $W/a2chunk
while :; do
  left=$(python3 - <<EOF
import json
rows=[l.split("\t")[0] for l in open("$W/manifest.tsv") if not l.startswith("#")]
done=set()
for l in open("$LOG"):
    d=json.loads(l)
    if "err" not in d: done.add(d["p"])
print(sum(1 for r in rows if r not in done))
EOF
)
  echo "$(date -u +%H:%M) $left files left" >> $W/drive2.log
  [ "$left" -eq 0 ] && break
  python3 -m deid.run --manifest $W/manifest.tsv --registry $W/registry2.json --out $W/a2chunk$k \
      --log $LOG --jobs 6 --budget-gb 30 >> $W/a2run$k.log 2>&1
  up $W/a2chunk$k
  k=$((k+1))
done
echo "$(date -u +%H:%M) ALL CORPUS CHUNKS UPLOADED" >> $W/drive2.log
