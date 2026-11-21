#!/usr/bin/env bash
# upload whatever is finished in the current chunk (hard-link snapshot, no partial files)
cd /home/user/work
d=$(ls -d chunk[0-9]* 2>/dev/null | tail -1); [ -z "$d" ] && exit 0
rm -rf snap && cp -al "$d" snap && find snap -name '*.part~' -delete
MACHINA_UPLOAD_TOKEN="$(cat secret/token.txt)" python3 upload.py --dest "$(cat dest.txt)" --corpus snap --workers 48 > upload_snap.log 2>&1
rc=$?; grep -E "^Found" upload_snap.log; tail -1 upload_snap.log; rm -rf snap; exit $rc
