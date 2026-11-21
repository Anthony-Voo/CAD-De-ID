#!/usr/bin/env bash
# Build shards I..J of N into /home/user/deid-out/attempt1/shard-I (one key for all shards).
set -u
N=$1; shift
export DEID_KEY=$(cat ~/.deid/key)
for i in "$@"; do
  out=/home/user/deid-out/attempt1/shard-$i
  [ -f "$out/DONE" ] && { echo "shard $i already done"; continue; }
  echo "=== shard $i/$N start $(date -u +%H:%M:%S)"
  python3 -I /home/user/cad-de-id/run.py --manifest /home/user/goonies-buildathon/tracks/machina-deid/kit/manifest.tsv \
    --out "$out" --shard "$i/$N" --workers 16 --ids /home/user/cad-de-id/private/ids.json 2>&1 | grep -E "done:|Error|Traceback" 
  du -sh "$out/corpus" && touch "$out/DONE"
  echo "=== shard $i done $(date -u +%H:%M:%S)"
done
