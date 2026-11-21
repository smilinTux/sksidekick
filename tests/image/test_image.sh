#!/usr/bin/env bash
# End-to-end check of a built sksidekick image: tests/image/test_image.sh IMAGE
# No pipefail: every check pipes into grep -q, which exits early and makes the writer die of SIGPIPE (141),
# turning a match into a failure, or worse, hiding a match in a "should not appear" check.
set -eu
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
wait_for_quiet() {  # like wait_for, but returns 1 instead of failing
    local n="$1"; shift
    for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    return 1
}
procs() { docker exec "$C" sh -c 'for p in /proc/[0-9]*/cmdline; do printf "%s: " "${p#/proc/}"; tr "\0" " " < "$p" 2>/dev/null; echo; done'; }
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

ver="$(docker exec "$C" hermes --version 2>&1 || true)"
ver="${ver%%$'\n'*}"
echo "hermes --version: $ver"
# A Docker build of the fork has no git metadata, so it prints "vunknown (<release date>)";
# image.yml already checked the tag's commit, so match the pinned release date.
want="$(sed -n 's/^HERMES_RELEASE_DATE=//p' "$(dirname "$0")/../../packaging/versions.env")"
case "$ver" in *"($want)"*) ;; *) fail "wrong Hermes build: $ver (want release $want)";; esac
docker exec "$C" sh -c 'command -v skmemory-mcp' >/dev/null || fail "skmemory-mcp not on PATH"
docker exec "$C" test -f /opt/hermes/plugins/memory/skmemory/plugin.yaml || fail "plugin not bundled"
ok "Hermes sk.4, skmemory-mcp and the bundled plugin are present"

wait_for 60 "memory.provider skmemory" sh -c "docker exec '$C' sidekick-hermes config get memory.provider | grep -qx skmemory"
docker exec "$C" test -f /opt/data/sidekick/hermes/skmemory.json || fail "skmemory.json missing"
ok "the sidekick's Hermes home uses the skmemory provider"

wait_for 60 "sksidekick serve" serve_running
echo "processes matching gateway or serve while live:"; procs | grep -E 'gateway|sksidekick serve' || true
ok "serve is supervising (a gateway with no channel set up may exit and be restarted with backoff)"

docker exec "$C" sksidekick backup add-dir /opt/data/usb >/dev/null
docker exec "$C" sksidekick backup run >/dev/null || fail "backup run failed"
docker exec "$C" sksidekick drill | grep -q '"ok": true' || fail "drill failed"
ok "backup and restore drill pass"

docker logs "$C" 2>&1 | grep -qF "$phrase" && fail "the recovery phrase reached the container log"
ok "the phrase is not in the container log"

docker exec "$C" sksidekick move-out /opt/data/move.sksk >/dev/null
stopped() { [ "$(count 'gateway run|sksidekick serve')" = 0 ]; }
# serve sees the move within 5 s and gives the gateway 20 s to stop cleanly (design limit: 60 s)
if ! wait_for_quiet 40 stopped; then
    echo "still running after move-out:"; procs | grep -E 'gateway|sksidekick serve' || true
    fail "serve or the gateway still runs 40 s after move-out"
fi
docker inspect -f '{{.State.Running}}' "$C" | grep -qx true || fail "the moved-out container exited (would crash loop under a restart policy)"
ok "move-out stops the gateway and the container idles"
echo "all image checks passed"
