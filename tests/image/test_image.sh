#!/usr/bin/env bash
# End-to-end check of a built sksidekick image: tests/image/test_image.sh IMAGE
set -euo pipefail
IMG="${1:?usage: test_image.sh IMAGE}"
C="sksidekick-test-$$"
VOL="$(docker volume create)"
cleanup() { docker rm -f "$C" >/dev/null 2>&1 || true; docker volume rm -f "$VOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; docker logs "$C" 2>&1 | tail -40 >&2 || true; exit 1; }
ok() { echo "ok: $*"; }
wait_for() {  # wait_for SECONDS DESCRIPTION COMMAND...
    local n="$1" what="$2"; shift 2
    for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "timed out waiting for $what"
}
procs() { docker exec "$C" sh -c 'for p in /proc/[0-9]*/cmdline; do tr "\0" " " < "$p" 2>/dev/null; echo; done'; }
count() { procs | grep -cE "$1" || true; }
serve_running() { [ "$(count 'sksidekick serve')" -ge 1 ]; }

docker run -d --name "$C" -v "$VOL:/opt/data" "$IMG" >/dev/null
wait_for 180 "the no-sidekick message" sh -c "docker logs '$C' 2>&1 | grep -q 'No sidekick here yet'"
ok "waits for a sidekick instead of crash looping"

out="$(docker exec "$C" sksidekick init)"
phrase="$(printf '%s\n' "$out" | grep -oE '([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}' | head -1)"
[ -n "$phrase" ] || fail "init printed no recovery phrase"
ok "init printed a recovery phrase"

docker exec "$C" sh -c 'stat -c %U /opt/data/sidekick/keys/live.identity' | grep -qx hermes || fail "keys are not owned by hermes"
ok "files belong to the hermes user"

docker exec "$C" hermes --version | grep -q "0.23.1-sk.4" || fail "wrong Hermes version"
docker exec "$C" sh -c 'command -v skmemory-mcp' >/dev/null || fail "skmemory-mcp not on PATH"
docker exec "$C" test -f /opt/hermes/plugins/memory/skmemory/plugin.yaml || fail "plugin not bundled"
ok "Hermes sk.4, skmemory-mcp and the bundled plugin are present"

wait_for 60 "memory.provider skmemory" sh -c "docker exec '$C' sidekick-hermes config get memory.provider | grep -qx skmemory"
docker exec "$C" test -f /opt/data/sidekick/hermes/skmemory.json || fail "skmemory.json missing"
ok "the sidekick's Hermes home uses the skmemory provider"

wait_for 60 "sksidekick serve" serve_running
ok "serve is supervising (a gateway with no channel set up may exit and be restarted with backoff)"

docker exec "$C" sksidekick backup add-dir /opt/data/usb >/dev/null
docker exec "$C" sksidekick backup run >/dev/null || fail "backup run failed"
docker exec "$C" sksidekick drill | grep -q '"ok": true' || fail "drill failed"
ok "backup and restore drill pass"

docker logs "$C" 2>&1 | grep -qF "$phrase" && fail "the recovery phrase reached the container log"
ok "the phrase is not in the container log"

docker exec "$C" sksidekick move-out /opt/data/move.sksk >/dev/null
sleep 15
[ "$(count 'gateway run|sksidekick serve')" = 0 ] || fail "serve or the gateway still runs 15 s after move-out"
docker inspect -f '{{.State.Running}}' "$C" | grep -qx true || fail "the moved-out container exited (would crash loop under a restart policy)"
ok "move-out stops the gateway and the container idles"
echo "all image checks passed"
