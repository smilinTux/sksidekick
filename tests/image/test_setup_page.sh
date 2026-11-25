#!/usr/bin/env bash
# First run of the image through the setup page, every step over HTTP as the browser would:
#   tests/image/test_setup_page.sh IMAGE
# The container publishes the setup port on the host's loopback only. After the walk (tests/setup_walk.sh)
# the sidekick serves, the recovery phrase is in no file under /opt/data and in no log line, and a real
# Hermes turn against the model chosen in setup is stored as a memory and survives a backup and restore.
# No pipefail: checks pipe into grep -q (see test_image.sh).
set -eu
IMG="${1:?usage: test_setup_page.sh IMAGE}"
here="$(cd "$(dirname "$0")" && pwd)"
C="sksidekick-test-$$"
VOL="$(docker volume create)"
PORT=18765
cleanup() { docker rm -f "$C" >/dev/null 2>&1 || true; docker volume rm -f "$VOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; docker logs "$C" 2>&1 | grep -v '?t=' | tail -40 >&2 || true; exit 1; }
ok() { echo "ok: $*"; }
wait_for() {  # wait_for SECONDS DESCRIPTION COMMAND...
    local n="$1" what="$2"; shift 2
    for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "timed out waiting for $what"
}
procs() { docker exec "$C" sh -c 'for p in /proc/[0-9]*/cmdline; do printf "%s: " "${p#/proc/}"; tr "\0" " " < "$p" 2>/dev/null; echo; done'; }
count() { procs | grep -cE "$1" || true; }
serve_running() { [ "$(count 'sksidekick serve')" -ge 1 ]; }

docker run -d --name "$C" -p "127.0.0.1:$PORT:8765" -v "$VOL:/opt/data" "$IMG" >/dev/null
wait_for 180 "the setup address" sh -c "docker logs '$C' 2>&1 | grep -q 'Open http://127.0.0.1:8765/?t='"
docker logs "$C" 2>&1 | grep -q 'No sidekick here yet' || fail "no first-run message"
docker logs "$C" 2>&1 | grep -qF -- '-p 127.0.0.1:8765:8765' || fail "the first-run message does not say how to publish the port"
tok="$(docker logs "$C" 2>&1 | sed -n 's|.*Open http://127.0.0.1:8765/?t=\([A-Za-z0-9_-]*\).*|\1|p' | tail -1)"
[ -n "$tok" ] || fail "no setup token in the log"
[ "$(docker port "$C" 8765)" = "127.0.0.1:$PORT" ] || fail "the setup port is not on the host's loopback only"
base="http://127.0.0.1:$PORT"
wait_for 30 "the setup page" curl -sf -o /dev/null "$base/?t=$tok"
ok "the image starts the setup page, published on loopback"

# The Nextcloud Talk fields must be the nctalk plugin's own env names, when the image carries the plugin;
# without it setup must offer the channel as disabled.
jar="$(mktemp)"
curl -sS -c "$jar" -o /dev/null "$base/?t=$tok"
st="$(curl -sS -b "$jar" "$base/api/state")"
rm -f "$jar"
nc_state="$(printf '%s' "$st" | python3 -c '
import json, sys
c = next(c for c in json.load(sys.stdin)["channels"] if c["id"] == "nextcloud-talk")
print(("on " if c["enabled"] else "off ") + " ".join(c["fields"]))')"
plugin="$(docker exec "$C" sh -c 'find / -xdev -type d -path "*plugins*" -name nctalk 2>/dev/null | head -1')"
if [ -n "$plugin" ]; then
    for name in ${nc_state#* }; do
        docker exec "$C" grep -rqF "$name" "$plugin" || fail "the nctalk plugin at $plugin never reads $name"
    done
    ok "the Nextcloud Talk fields are the plugin's env names"
else
    case "$nc_state" in off\ *) ;; *) fail "Nextcloud Talk is offered but the image has no nctalk plugin";; esac
    ok "no nctalk plugin in the image; setup offers Nextcloud Talk as disabled"
fi

# The model setup checks: a stub inside the container (setup's reachability check runs there).
docker cp "$here/stub_model.py" "$C:/tmp/stub_model.py"
docker exec -d -u hermes "$C" /opt/sksidekick/bin/python /tmp/stub_model.py 18999
docker exec -u hermes "$C" mkdir -p /opt/data/usb
wait_for 20 "the stub model" docker exec "$C" /opt/sksidekick/bin/python -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:18999/v1/models", timeout=2)'

# A seed setting must survive setup: setup layers its choices on the seeded config.yaml, as after init.
docker exec -u hermes -e HERMES_HOME=/opt/data "$C" hermes config set terminal.timeout 177 >/dev/null \
    || fail "could not set a marker in the Hermes seed"
docker exec "$C" test ! -e /opt/data/sidekick/hermes || fail "the sidekick's Hermes home exists before any setup step"

exec 4>&1
phrase="$(SETUP_WALK_BACKUP_DIR=/opt/data/usb bash "$here/../setup_walk.sh" "$base" "$tok" 3>&1 1>&4)" \
    || fail "the setup walk failed"
exec 4>&-
printf '%s\n' "$phrase" | grep -qE '^([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}$' || fail "the walk handed back no phrase"
ok "every setup step answered over HTTP"

wait_for 60 "sksidekick serve" serve_running
wait_for 20 "setup to stop listening" sh -c "! curl -s -o /dev/null '$base/'"
docker exec "$C" sh -c 'stat -c %U /opt/data/sidekick/keys/live.identity' | grep -qx hermes || fail "keys are not owned by hermes"
ok "setup closed and serve is running"

docker exec -u hermes "$C" sidekick-hermes config get terminal.timeout 2>/dev/null | grep -qx 177 \
    || fail "the seed's terminal.timeout did not survive setup"
docker exec -u hermes "$C" sidekick-hermes config get model.default 2>/dev/null | grep -qx stub \
    || fail "setup's model choice is not in the sidekick's Hermes config"
wait_for 30 "memory.provider skmemory" sh -c "docker exec -u hermes '$C' sidekick-hermes config get memory.provider | grep -qx skmemory"
ok "setup layered on the Hermes seed (seed setting kept, model chosen, skmemory provider)"

patterns="$(printf '%s\n%s\n' "$phrase" "$(printf '%s' "$phrase" | tr -d -)")"
if printf '%s\n' "$patterns" | docker exec -i "$C" grep -rlF -f - /opt/data >/dev/null 2>&1; then
    fail "the recovery phrase was written to a file under /opt/data"
fi
if docker logs "$C" 2>&1 | grep -qF -f <(printf '%s\n' "$patterns"); then
    fail "the recovery phrase is in the container log"
fi
ok "the phrase is in no file under /opt/data and in no log line"

# One real turn against the model setup chose (the same check as test_memory_turn.sh).
store=/opt/data/sidekick/hermes/.skmemory/agents/sidekick/memory
docker exec -u hermes -e OPENAI_API_KEY=stub -e CUSTOM_API_KEY=stub "$C" sidekick-hermes chat -Q \
    --provider custom --base-url http://127.0.0.1:18999/v1 -m stub -q "Remember that my fox is called Juniper." 2>&1 | tail -8 || true
remembered() { docker exec "$C" grep -rlq "Juniper" "$store"; }
wait_for 30 "the turn stored as a memory in $store" remembered
ok "a Hermes turn stored its memory inside the sidekick home"

docker exec "$C" sksidekick backup run >/dev/null || fail "backup run to the folder chosen in setup failed"
latest="$(docker exec "$C" sh -c 'ls -1 /opt/data/usb/*.sksk | tail -1')"
printf '%s\n' "$phrase" | docker exec -i "$C" sksidekick --home /tmp/restored import "$latest" --phrase-stdin >/dev/null \
    || fail "the backup did not import with the phrase from the setup page"
docker exec "$C" grep -rlq "Juniper" /tmp/restored/hermes/.skmemory/agents/sidekick/memory || fail "the backup lacks the memory"
ok "a backup to the folder chosen in setup restores with the phrase"
echo "setup page checks passed"
