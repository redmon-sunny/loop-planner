#!/bin/sh
# Pull WoW SavedVariables from the gaming desktop over SSH and run sv_parse.py (Loop Planner, option 1).
# Lives at backend/sv_pull.sh in the repo; runs on core-01 from cron. POSIX sh, needs only ssh/scp/python3.
#
# One-time on core-01 (after Enable-SvPull.ps1 ran on the desktop):
#   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ''          # only if you have no key yet
#   ssh <winuser>@<desktop> 'echo ok'                         # accept the host key once
#   add to .env:  SV_HOST=<winuser>@<desktop>
#                 SV_DIR='C:/Program Files (x86)/World of Warcraft/_classic_/WTF/Account/<ACCOUNT>/SavedVariables'
#   crontab -e:   */30 * * * *  cd $HOME/srv/loop-planner && sh backend/sv_pull.sh >> state/sv_pull.log 2>&1
#
# Behaviour:
#   - copies Auctionator.lua and ForeverDB.lua into state/sv/ (git-ignored, never served) only when the remote file is
#     newer than the local copy, so a run while nothing changed costs one ssh round-trip and no parse
#   - desktop asleep/offline: exits 0 quietly (cron stays silent); any other error is logged and exits 1
#   - after a pull, runs sv_parse.py into data/ (what nginx serves; the page picks it up on next load)
#   - file mtimes are preserved (scp -p), so prices.json's fileModified is the real scan time
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
: "${SV_HOST:?set SV_HOST=user@desktop in .env}"
: "${SV_DIR:?set SV_DIR='C:/.../WTF/Account/<ACCOUNT>/SavedVariables' in .env}"
SV_FILES="${SV_FILES:-Auctionator.lua ForeverDB.lua}"
DEST=state/sv
mkdir -p "$DEST" data
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

# reachability: desktop off or asleep is normal, not an error
if ! $SSH "$SV_HOST" 'exit 0' 2>/dev/null; then
    exit 0
fi

# remote mtimes in one call (PowerShell on the desktop; epoch seconds, one line per file: "name mtime" or "name -")
remote_list=$($SSH "$SV_HOST" "powershell -NoProfile -Command \"\$d='$SV_DIR'; foreach(\$f in '$SV_FILES'.Split(' ')){ \$p=Join-Path \$d \$f; if(Test-Path \$p){ '{0} {1}' -f \$f,[int64](Get-Date (Get-Item \$p).LastWriteTimeUtc -UFormat %s) } else { '{0} -' -f \$f } }\"") || { log "ERROR listing $SV_DIR on $SV_HOST"; exit 1; }

pulled=""
echo "$remote_list" | tr -d '\r' | while read -r name mtime; do
    [ -n "$name" ] || continue
    if [ "$mtime" = "-" ]; then log "$name: not on desktop yet"; continue; fi
    local_mtime=0
    [ -f "$DEST/$name" ] && local_mtime=$(stat -c %Y "$DEST/$name")
    if [ "$mtime" -gt "$local_mtime" ]; then
        if scp -p -q -o BatchMode=yes "$SV_HOST:\"$SV_DIR/$name\"" "$DEST/$name.tmp" && [ -s "$DEST/$name.tmp" ]; then
            mv "$DEST/$name.tmp" "$DEST/$name"
            log "$name: pulled ($(wc -c < "$DEST/$name") bytes, scan $(date -u -d @"$mtime" +%FT%TZ))"
            echo "$name" >> "$DEST/.pulled"
        else
            rm -f "$DEST/$name.tmp"; log "ERROR $name: scp failed"
        fi
    fi
done

[ -f "$DEST/.pulled" ] || exit 0
pulled=$(sort -u "$DEST/.pulled"); rm -f "$DEST/.pulled"

args=""
echo "$pulled" | grep -q '^Auctionator.lua$' && [ -f "$DEST/Auctionator.lua" ] && args="$args --auctionator $DEST/Auctionator.lua"
echo "$pulled" | grep -q '^ForeverDB.lua$'   && [ -f "$DEST/ForeverDB.lua" ]   && args="$args --foreverdb $DEST/ForeverDB.lua"
[ -f observed.csv ] && args="$args --observed-csv observed.csv"
if [ -n "$args" ]; then
    # shellcheck disable=SC2086
    python3 backend/sv_parse.py $args --out data && log "parsed:$args" || log "ERROR sv_parse failed (file kept at $DEST for --dump)"
fi
