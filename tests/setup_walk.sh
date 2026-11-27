#!/usr/bin/env bash
# Walk every first-run setup step with curl, as the page would:
#   tests/setup_walk.sh BASE_URL TOKEN
# BASE_URL is where setup answers (http://127.0.0.1:18765); TOKEN is the ?t= value it printed.
# Steps: the private tier against a model at $SETUP_WALK_MODEL_URL (tests/image/stub_model.py; it must be
# reachable from the setup server, default http://127.0.0.1:18999/v1), zero channels, mentor + female,
# a new phrase confirmed with the two groups it asks for, backups to the folder $SETUP_WALK_BACKUP_DIR
# (a path as the setup server sees it). SETUP_WALK_TIER=shared picks the seat's shared gateway instead of the
# private tier (setup must have been started with --invite), with model $SETUP_WALK_MODEL. Used by tests/image/test_setup_page.sh and the installer test.
# The phrase is never printed. If file descriptor 3 is open, the phrase is written there (and only there)
# so the caller can check that it appears in no file and no log.
set -eu
base="${1:?usage: setup_walk.sh BASE_URL TOKEN}"
tok="${2:?usage: setup_walk.sh BASE_URL TOKEN}"
model_url="${SETUP_WALK_MODEL_URL:-http://127.0.0.1:18999/v1}"
backup_dir="${SETUP_WALK_BACKUP_DIR:?set SETUP_WALK_BACKUP_DIR to a folder the setup server can write}"
base="${base%/}"

jar="$(mktemp)"
trap 'rm -f "$jar"' EXIT
fail() { echo "FAIL: setup walk: $*" >&2; exit 1; }

# json EXPR: run a python expression on the JSON read from stdin (python3 is on every runner and in the image)
json() { python3 -c 'import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }

# post PATH BODY: prints the response body; fails on any status but 200
post() {
    local out status
    out="$(curl -sS -b "$jar" -H "X-Setup-Token: $tok" -H "Content-Type: application/json" \
        --data-binary "$2" -w '\n%{http_code}' "$base/$1")" || fail "POST $1 did not connect"
    status="${out##*$'\n'}"
    out="${out%$'\n'*}"
    [ "$status" = 200 ] || fail "POST $1 answered $status: $(printf '%s' "$out" | head -c 300)"
    printf '%s' "$out"
}

status="$(curl -sS -c "$jar" -o /dev/null -w '%{http_code}' "$base/?t=$tok")" || fail "the page did not answer"
[ "$status" = 200 ] || fail "the page answered $status"
curl -sS -b "$jar" "$base/static/setup.js" | grep -q "X-Setup-Token" || fail "the page script is not served"
next="$(curl -sS -b "$jar" "$base/api/state" | json 'd["next"]')"
[ "$next" = tier ] || fail "setup does not start at the model step (next: $next)"
echo "ok: setup page and state answer"

if [ "${SETUP_WALK_TIER:-private}" = shared ]; then
    post api/tier "{\"tier\": \"shared\", \"model\": \"${SETUP_WALK_MODEL:-stub}\"}" >/dev/null
else
    post api/tier "{\"tier\": \"private\", \"base_url\": \"$model_url\", \"model\": \"stub\"}" >/dev/null
fi
post api/channels '{"channels": []}' >/dev/null
post api/personality '{"type": "mentor", "voice": "female"}' >/dev/null
echo "ok: model, channels and personality saved"

# the phrase stays in this variable; only the two asked-for groups are sent back
new="$(post api/phrase/new '{}')"
body="$(printf '%s' "$new" | python3 -c '
import json, sys
d = json.load(sys.stdin)
groups = d["phrase"].split("-")
print(json.dumps({"groups": {str(i): groups[i] for i in d["check"]}}))')"
post api/phrase/confirm "$body" >/dev/null
if { true >&3; } 2>/dev/null; then
    printf '%s' "$new" | json 'd["phrase"]' >&3
fi
new=""
body=""
echo "ok: recovery phrase confirmed"

post api/backup "{\"kind\": \"dir\", \"target\": \"$backup_dir\"}" >/dev/null
echo "ok: backups go to $backup_dir; setup is done"
