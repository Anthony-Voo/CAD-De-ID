#!/usr/bin/env bash
# Driver for attempt 1: finish the current chunk, then process/upload/delete
# the remaining corpus in chunks.  Token lives in secret/token.txt (never printed).
set -uo pipefail
W=/home/user/work
cd /home/user/deid-next
export PYTHONPATH=/home/user/deid-next
DEST=$(cat $W/dest3.txt)
LOG=$W/run-attempt-3.jsonl
up() {  # upload a directory as corpus/, then delete it
  local d="$1"
  find "$d" -name '*.part~' -delete
  until MACHINA_UPLOAD_TOKEN="$(cat $W/secret/token.txt)" python3 $W/upload.py --dest "$DEST" --corpus "$d" \
        --token-file $W/secret/token.txt --workers 32 >> $W/upload.log 2>&1; do
    echo "$(date -u +%H:%M) upload of $d stopped (token?); waiting for a new token" >> $W/drive3.log
    old=$(md5sum < $W/secret/token.txt)
    until [ "$(md5sum < $W/secret/token.txt)" != "$old" ]; do sleep 15; done
  done
  echo "$(date -u +%H:%M) uploaded $d" >> $W/drive3.log
  rm -rf "$d"
}
# resume: continue in the highest existing chunk folder (run.py skips files
# already in the log; partial *.part~ files are removed before upload)
touch $LOG
k=2
for d in $W/a3chunk[0-9]*; do [ -d "$d" ] && n=${d##*chunk} && [ "$n" -gt "$k" ] && k=$n; done
# finished chunks from before a restart: upload them in the background;
# the newest one is completed first (run.py resumes from the log)
for d in $W/a3chunk[0-9]*; do
  [ -d "$d" ] || continue
  n=${d##*chunk}
  [ "$n" -lt "$k" ] && ( up "$d" ) &
done
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
  echo "$(date -u +%H:%M) $left files left" >> $W/drive3.log
  [ "$left" -eq 0 ] && break
  # keep processing while uploads wait for a token: only start a chunk when
  # there is room for it (~25 GB input -> ~25 GB output, keep a margin)
  while [ "$(df --output=avail -BG /home/user | tail -1 | tr -dc 0-9)" -lt 34 ]; do sleep 30; done
  python3 -m deid.run --manifest $W/manifest.tsv --registry $W/registry3.json --out $W/a3chunk$k \
      --log $LOG --jobs 6 --budget-gb 25 >> $W/a3run$k.log 2>&1
  ( up $W/a3chunk$k ) &
  k=$((k+1))
done
wait
echo "$(date -u +%H:%M) ALL CORPUS CHUNKS UPLOADED" >> $W/drive3.log
