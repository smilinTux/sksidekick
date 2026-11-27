#!/usr/bin/env bash
# The invite as docs/SEATS.md says to give it to a container (issue #8): the operator's own file, mode 0600,
# bind mounted read only, with SKSIDEKICK_INVITE pointing at the mount. Setup runs as hermes (uid 10000) and
# cannot read that file; the image's root start step copies it into the volume for setup.
#   tests/image/test_invite_mount.sh IMAGE
# Also the layout copied in by hand (/opt/data/invite.json, owned by hermes, 0600), which must keep working.
# No pipefail: checks pipe into grep -q (see test_image.sh).
set -eu
IMG="${1:?usage: test_invite_mount.sh IMAGE}"
C="sksidekick-test-$$"
VOL="$(docker volume create)"
PORT=18766
tmp="$(mktemp -d)"
cleanup() { docker rm -f "$C" >/dev/null 2>&1 || true; docker volume rm -f "$VOL" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; docker logs "$C" 2>&1 | grep -v '?t=' | tail -40 >&2 || true; exit 1; }
ok() { echo "ok: $*"; }
wait_for() {  # wait_for SECONDS DESCRIPTION COMMAND...
    local n="$1" what="$2"; shift 2
    for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    fail "timed out waiting for $what"
}

SECRET="tok-invite-mount-$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
umask 077
python3 - "$tmp/invite.json" "$SECRET" <<'PY'
import json, sys
json.dump({"kind": "sksidekick-invite", "version": 1, "gateway_url": "https://gateway.example.com/v1",
           "agent_id": "sidekick", "client_id": "sk-0123456789ab", "credential_revision": "r1",
           "token": sys.argv[2], "expires_at": "2099-01-01T00:00:00Z"}, open(sys.argv[1], "w"))
PY
chmod 0600 "$tmp/invite.json"
[ "$(id -u)" != 10000 ] || fail "run this as a user other than uid 10000"

shared_host() {  # what setup's state says about the shared option: "on HOST" or "off"
    local tok jar st
    tok="$(docker logs "$C" 2>&1 | sed -n 's|.*Open http://127.0.0.1:8765/?t=\([A-Za-z0-9_-]*\).*|\1|p' | tail -1)"
    jar="$(mktemp)"
    curl -sS -c "$jar" -o /dev/null "http://127.0.0.1:$PORT/?t=$tok"
    st="$(curl -sS -b "$jar" "http://127.0.0.1:$PORT/api/state")"
    rm -f "$jar"
    printf '%s' "$st" | python3 -c '
import json, sys
sh = json.load(sys.stdin)["tiers"]["shared"]
print(("on " + sh.get("host", "")) if sh.get("enabled") else "off")'
}

start() {  # start EXTRA_DOCKER_ARGS...
    docker run -d --name "$C" -p "127.0.0.1:$PORT:8765" -v "$VOL:/opt/data" "$@" "$IMG" >/dev/null
    wait_for 180 "the setup address" sh -c "docker logs '$C' 2>&1 | grep -q 'Open http://127.0.0.1:8765/?t='"
    local tok
    tok="$(docker logs "$C" 2>&1 | sed -n 's|.*Open http://127.0.0.1:8765/?t=\([A-Za-z0-9_-]*\).*|\1|p' | tail -1)"
    wait_for 30 "the setup page" curl -sf -o /dev/null "http://127.0.0.1:$PORT/?t=$tok"
}

# 1. The documented way: the operator's 0600 file, read only.
start -v "$tmp/invite.json:/run/invite:ro" -e SKSIDEKICK_INVITE=/run/invite
docker logs "$C" 2>&1 | grep -q 'could not read the invite file' && fail "setup could not read the mounted invite"
[ "$(shared_host)" = "on gateway.example.com" ] || fail "setup does not offer the shared option from the mounted invite"
[ "$(docker exec "$C" stat -c '%U %a' /opt/data/invite.json)" = "hermes 600" ] || fail "the copy is not hermes-owned 0600"
docker logs "$C" 2>&1 | grep -qF "$SECRET" && fail "the invite token reached the container log"
ok "a 0600 invite on a read only mount reaches setup through a private copy"
docker rm -f "$C" >/dev/null
docker volume rm -f "$VOL" >/dev/null
VOL="$(docker volume create)"

# 2. The layout copied in by hand, as used before the fix.
docker run --rm -v "$VOL:/opt/data" -v "$tmp/invite.json:/in:ro" --entrypoint sh "$IMG" \
    -c 'cp /in /opt/data/invite.json && chown 10000:10000 /opt/data/invite.json && chmod 0600 /opt/data/invite.json'
start -e SKSIDEKICK_INVITE=/opt/data/invite.json
docker logs "$C" 2>&1 | grep -q 'could not read the invite file' && fail "setup could not read the hand-copied invite"
docker logs "$C" 2>&1 | grep -q 'private copy' && fail "a readable invite was copied again"
[ "$(shared_host)" = "on gateway.example.com" ] || fail "setup does not offer the shared option from the hand-copied invite"
ok "an invite copied into the volume by hand is used where it is"
echo "all invite mount checks passed"
