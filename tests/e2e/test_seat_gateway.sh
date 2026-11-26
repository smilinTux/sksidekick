#!/usr/bin/env bash
# End to end: a seated sidekick against a real skgateway with client auth.
#   tests/e2e/test_seat_gateway.sh
# A plain install (packaging/install.sh --no-init, real Hermes) on this machine, a skgateway checkout at
# $SKGATEWAY_REF on 127.0.0.1 with client_auth on, privacy.content_logs false and evidence and budget
# monitoring on, in front of a stub model (tests/image/stub_model.py). The operator side runs
# sksidekick-seat init and add; the sidekick's setup takes the invite and the shared tier (tests/setup_walk.sh);
# one real Hermes turn carries a canary. Checks: the stub saw the turn; /admin/clients/usage counts it under
# the seat's client id and sksidekick-seat usage names the owner; the canary and the token are in no
# gateway file and not in its output; after sksidekick-seat remove and SIGHUP the gateway answers 401 and
# the next Hermes turn fails with it.
# Needs: git, node 22 with npm, python3, curl. Linux CI runs it on a fresh HOME (see .github/workflows/seats.yml).
# No pipefail: checks pipe into grep -q (see tests/image/test_image.sh).
set -eu
repo="$(cd "$(dirname "$0")/../.." && pwd)"
SKGATEWAY_REPO="${SKGATEWAY_REPO:-https://github.com/smilinTux/skgateway}"
# Pinned to the commit of release tag skgateway-v1.1.23 (client auth metering, privacy.content_logs).
SKGATEWAY_REF="${SKGATEWAY_REF:-69b8e43c10ac77269cf9b8bc84d1a2f1006340de}"
MODEL=seat-stub
ESTATE=e2e
OWNER="Example Seat Holder"
CANARY="SEATCANARY$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
unset SKWORLD_ESTATE  # the seats record below is for estate $ESTATE only

work="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/seat-e2e.XXXXXX")"
H="$work/sidekick home"
gw="$work/gateway"           # the gateway's HOME, cwd, logs and state: everything it may write
gwsrc="$work/skgateway"      # its code
mkdir -p "$gw/cluster" "$gw/logs" "$gw/state" "$work/usb"
pids=""
cleanup() {
    for p in $pids; do kill "$p" 2>/dev/null || true; done
    sleep 1
    rm -rf "$work"
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }
free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
if [ "$(uname -s)" = Darwin ]; then app="$HOME/Library/Application Support/sksidekick-app"; else app="${XDG_DATA_HOME:-$HOME/.local/share}/sksidekick-app"; fi
seat() { "$app/sksidekick/bin/sksidekick-seat" --seats-dir "$work/seats" --estate "$ESTATE" "$@"; }
# The service's environment (packaging/sksidekick-setup.sh), for Hermes run by hand.
hermes() {
    env PATH="$app/bin:/usr/local/bin:/usr/bin:/bin" SKSIDEKICK_HOME="$H" HERMES_HOME="$H/hermes" \
        SIDEKICK_SEED="$app/hermes-seed" SIDEKICK_BIN="$app/sksidekick/bin" SIDEKICK_SKMEMORY_JSON="$app/skmemory.json" \
        "$app/bin/hermes" "$@"
}
# no_needle NEEDLE WHAT PATH...: the needle (passed on stdin, never on a command line) is in no file under PATH.
no_needle() {
    local n="$1" what="$2" out; shift 2
    out="$(printf '%s\n' "$n" | sh "$repo/tests/leak_search.sh" "$@")" || fail "could not search $* for the $what"
    [ -z "$out" ] || fail "the $what was written to: $out"
}

# 1. skgateway at the pinned commit, and the stub model behind it.
git init -q "$gwsrc"
git -C "$gwsrc" fetch -q --depth 1 "$SKGATEWAY_REPO" "$SKGATEWAY_REF" || fail "could not fetch skgateway $SKGATEWAY_REF"
git -C "$gwsrc" checkout -q "$SKGATEWAY_REF"
[ "$(git -C "$gwsrc" rev-parse HEAD)" = "$SKGATEWAY_REF" ] || fail "skgateway is not at $SKGATEWAY_REF"
(cd "$gwsrc" && npm ci --omit=dev --no-audit --no-fund --loglevel=error) >"$work/npm.log" 2>&1 \
    || { tail -30 "$work/npm.log"; fail "npm ci for skgateway failed"; }
ok "skgateway at $SKGATEWAY_REF"
stub_port="$(free_port)"
STUB_MODEL_RECORD="$work/backend.jsonl" python3 "$repo/tests/image/stub_model.py" "$stub_port" >"$work/stub.log" 2>&1 &
pids="$pids $!"

# 2. A plain install with real Hermes, no sidekick yet (setup runs below with the invite).
bash "$repo/packaging/install.sh" --home "$H" --no-init >"$work/install.log" 2>&1 \
    || { tail -60 "$work/install.log"; fail "install.sh failed"; }
[ -x "$app/sksidekick/bin/sksidekick-seat" ] || fail "sksidekick-seat is not in the install"
ok "plain install with real Hermes"

# 3. The operator: a seats record and one seat, the invite pointing at the gateway on loopback.
gw_port="$(free_port)"
seat init >/dev/null
out="$(seat add --owner "$OWNER" --place linux --registry "$gw/clients.json" \
    --gateway-url "http://127.0.0.1:$gw_port/v1" --invite-out "$work/invite.json")" || fail "sksidekick-seat add failed"
cid="$(printf '%s\n' "$out" | sed -n 's/^seat \(sk-[0-9a-f]*\) for .*/\1/p')"
[ -n "$cid" ] || fail "sksidekick-seat add printed no client id"
[ "$(stat -c %a "$gw/clients.json" 2>/dev/null || stat -f %Lp "$gw/clients.json")" = 600 ] || fail "the registry is not 0600"
ok "seat $cid added; registry written"

# 4. The gateway, configured like a sidekick gateway (docs/SEATS.md). With client_auth on, the admin routes
# (/admin/clients/usage) need an operator credential too: a synthetic one, its headers kept in a 0600 file.
python3 - "$gw/operators.json" "$work/operator.headers" <<'PY'
import hashlib, json, os, secrets, sys
tok = secrets.token_urlsafe(32)
reg = {"schema_version": 1, "revision": "op-r1", "credentials": [{"agent_id": "local-operator",
       "client_id": "seat-console", "token_sha256": hashlib.sha256(tok.encode()).hexdigest(),
       "credential_revision": "operator-r1", "revoked": False, "expires_at": "2099-01-01T00:00:00Z"}]}
for path, text in ((sys.argv[1], json.dumps(reg)), (sys.argv[2], f"Authorization: Bearer {tok}\n"
        "x-sk-operator-id: local-operator\nx-sk-operator-client-id: seat-console\n"
        "x-sk-operator-credential-revision: operator-r1\n")):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
PY
printf 'enabled: true\nexempt_backends: []\nfamilies: { stub: ["%s"] }\nalerts: { outbox: %s }\ndeepseek_balance_poll_minutes: 0\n' \
    "$MODEL" "$gw/logs/alerts.jsonl" > "$gw/cluster/caps.yaml"
cat > "$gw/cluster/gw.yaml" <<EOF
server:
  bind: 127.0.0.1
  port: $gw_port
  dashboard_port: $(free_port)
dashboard:
  enabled: false
discovery:
  enabled: false
identity:
  enabled: false
classification:
  enabled: true
  classifier: heuristic
metrics:
  enabled: true
  db_path: $gw/metrics.db
siem:
  enabled: true
  outputs:
    - type: file
      path: $gw/logs/audit.jsonl
evidence_budget_monitoring:
  enabled: true
  client_caps: caps.yaml
privacy:
  content_logs: false
client_auth:
  enabled: true
  credentials_file: "$gw/clients.json"
  expected_owner_uid: $(id -u)
  expected_group_gid: null
  agent_header: x-agent-id
  client_header: x-sk-client-id
  revision_header: x-sk-credential-revision
operator_auth:
  enabled: true
  credentials_file: "$gw/operators.json"
  expected_owner_uid: $(id -u)
  expected_group_gid: null
backends:
  stub:
    url: http://127.0.0.1:$stub_port/v1
    auth_type: none
    models: [$MODEL]
    priority: 1
EOF
(cd "$gw" && exec env -u SKGATEWAY_CLIENT_CAPS HOME="$gw" SKCAPSTONE_HOME="$gw/.skcapstone" \
    SKGATEWAY_MODEL_CATALOG_STORE_PATH="$gw/state/model_catalog_store.json" \
    SKGATEWAY_CAPACITY_STORE_PATH="$gw/state/capacity_store.json" \
    node "$gwsrc/src/index.mjs" --config "$gw/cluster/gw.yaml") >"$work/gateway.out" 2>&1 &
gw_pid=$!
pids="$pids $gw_pid"
for _ in $(seq 30); do grep -q "\[skgateway\] listening" "$work/gateway.out" && break; kill -0 "$gw_pid" 2>/dev/null || break; sleep 1; done
grep -q "\[skgateway\] listening" "$work/gateway.out" || { tail -30 "$work/gateway.out"; fail "the gateway did not start"; }
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
    --data-binary "{\"model\": \"$MODEL\", \"messages\": [{\"role\": \"user\", \"content\": \"no credential\"}]}" \
    "http://127.0.0.1:$gw_port/v1/chat/completions")"
[ "$code" = 401 ] || fail "the gateway answered $code to a call without a credential (client auth is not on)"
ok "the gateway runs with client auth on (no credential: 401)"

# 5. The sidekick's setup with the invite: the shared tier, then the rest as tests/setup_walk.sh does.
"$app/bin/sksidekick-setup" --headless --invite "$work/invite.json" >"$work/setup.log" 2>&1 &
setup_pid=$!
pids="$pids $setup_pid"
url=""
for _ in $(seq 120); do
    url="$(sed -n 's|^Setup page (open it in a browser on this computer): ||p' "$work/setup.log" | tail -1)"
    [ -n "$url" ] && break
    kill -0 "$setup_pid" 2>/dev/null || { tail -30 "$work/setup.log"; fail "setup ended before its page started"; }
    sleep 1
done
[ -n "$url" ] || { tail -30 "$work/setup.log"; fail "setup printed no address"; }
SETUP_WALK_TIER=shared SETUP_WALK_MODEL="$MODEL" SETUP_WALK_BACKUP_DIR="$work/usb" \
    bash "$repo/tests/setup_walk.sh" "${url%%/\?t=*}" "${url##*\?t=}" || { tail -30 "$work/setup.log"; fail "the setup walk failed"; }
wait "$setup_pid" || { tail -30 "$work/setup.log"; fail "setup failed"; }
[ ! -e "$work/invite.json" ] || fail "setup kept the invite file (no --keep-invite)"
[ -f "$H/seat.json" ] || fail "setup wrote no seat record"
grep -q "\"$cid\"" "$H/seat.json" || fail "the seat record is not for $cid"
token="$(sed -n 's/^SKSIDEKICK_GATEWAY_TOKEN=//p' "$H/hermes/.env" | tail -1)"
[ -n "$token" ] || fail "the token is not in the sidekick's Hermes .env"
[ "$(hermes config get model.default)" = "$MODEL" ] || fail "Hermes does not use $MODEL"
ok "setup took the invite and the shared tier; the invite file is gone"

# 6. One real Hermes turn through the gateway, with the canary in it.
turn="$(hermes chat -Q -q "Please remember the code word $CANARY." 2>&1)" || { printf '%s\n' "$turn" | tail -20; fail "the Hermes turn failed"; }
printf '%s\n' "$turn" | grep -q "Juniper" || { printf '%s\n' "$turn" | tail -20; fail "Hermes did not answer with the stub's reply"; }
grep -q "$CANARY" "$work/backend.jsonl" 2>/dev/null || fail "the stub model never saw the turn"
ok "a Hermes turn went through the gateway to the model"

# 7. Metered under the seat's client id, and the operator's tool names the owner.
sleep 7  # metrics flush every 5 s
curl -sS -H @"$work/operator.headers" -o "$work/usage.json" "http://127.0.0.1:$gw_port/admin/clients/usage" || fail "/admin/clients/usage did not answer"
python3 - "$work/usage.json" "$cid" <<'EOF' || { head -c 2000 "$work/usage.json"; echo; fail "the usage does not count the turn under $cid"; }
import json, sys
d = json.load(open(sys.argv[1]))
cid = sys.argv[2]
today = [c for c in d["today"]["clients"] if c.get("client") == cid]
assert today and today[0].get("calls", 0) >= 1, d["today"]
others = [c["client"] for c in d["today"]["clients"] if c.get("client") != cid]
assert not others, f"usage under other clients: {others}"
rows = [r for r in (d.get("spend") or {}).get("rows") or [] if r.get("client") == cid]
assert rows and sum(r.get("calls") or 0 for r in rows) >= 1, d.get("spend")
EOF
seat usage --json "$work/usage.json" 2>/dev/null | grep -q "^$cid  $OWNER  calls=[1-9]" || { seat usage --json "$work/usage.json"; fail "sksidekick-seat usage does not show the owner"; }
ok "the turn is metered under $cid and sksidekick-seat usage names the owner"

# 8. No content and no token on the gateway.
no_needle "$CANARY" "canary (turn content)" "$gw" "$work/gateway.out"
no_needle "$token" "token" "$gw" "$work/gateway.out" "$work/setup.log" "$work/install.log"
ok "the canary and the token are in no gateway file and not in its output"

# 9. Remove the seat: revoke plus SIGHUP, and the next turn is refused with 401.
# The seat's own headers, from the sidekick's .env and seat.json, in a 0600 file (never on a command line).
python3 - "$H/hermes/.env" "$H/seat.json" "$work/seat.headers" <<'PY'
import json, os, sys
env = dict(l.split("=", 1) for l in open(sys.argv[1]).read().splitlines() if "=" in l)
rec = json.load(open(sys.argv[2]))
fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(f"Authorization: Bearer {env['SKSIDEKICK_GATEWAY_TOKEN'].strip()}\nx-agent-id: {rec['agent_id']}\n"
            f"x-sk-client-id: {rec['client_id']}\nx-sk-credential-revision: {rec['credential_revision']}\n")
PY
seat_models() { curl -s -o /dev/null -w '%{http_code}' -H @"$work/seat.headers" "http://127.0.0.1:$gw_port/v1/models"; }
code="$(seat_models)"
[ "$code" = 200 ] || fail "the seat's own credential got $code from the gateway before the remove, not 200"
seat remove "$cid" --registry "$gw/clients.json" >/dev/null || fail "sksidekick-seat remove failed"
kill -HUP "$gw_pid"
sleep 2
kill -0 "$gw_pid" 2>/dev/null || { tail -20 "$work/gateway.out"; fail "the gateway died on SIGHUP"; }
code="$(seat_models)"
[ "$code" = 401 ] || fail "the revoked credential got $code from the gateway, not 401"
ok "the same seat request: 200 before the remove, 401 after remove and SIGHUP"
# Only log lines written by the next turn count: note each Hermes log file's size now, read past it later.
find "$H/hermes/logs" -type f 2>/dev/null | while IFS= read -r f; do
    printf '%s\t%s\n' "$(wc -c < "$f" | tr -d ' ')" "$f"
done > "$work/log.sizes"
new_log_lines() {
    find "$H/hermes/logs" -type f 2>/dev/null | while IFS= read -r f; do
        n="$(awk -F '\t' -v f="$f" '$2 == f { print $1 }' "$work/log.sizes")"
        tail -c +"$(( ${n:-0} + 1 ))" "$f"
    done
}
seen="$(grep -c . "$work/backend.jsonl")"
rc=0
turn="$(hermes chat -Q -q "Second turn after removal $CANARY." 2>&1)" || rc=$?
[ "$(grep -c . "$work/backend.jsonl")" = "$seen" ] || fail "a turn reached the model after the seat was removed"
printf '%s\n' "$turn" | tail -5 | sed 's/^/  hermes: /'
logs="$(new_log_lines | grep -F client_auth_denied | tail -3 || true)"
printf '%s\n' "$logs" | sed 's/^/  log: /'
[ "$rc" != 0 ] || fail "the Hermes turn after the remove exited 0"
printf '%s\n' "$turn" | grep -qE "HTTP 401\b|client_auth_denied" || fail "the Hermes turn after the remove does not report the 401"
[ -n "$logs" ] || fail "no Hermes log line written by the refused turn names client_auth_denied"
ok "after remove and SIGHUP Hermes reports the gateway's 401 (exit $rc; client_auth_denied in its new log lines)"
no_needle "$CANARY" "canary (turn content)" "$gw" "$work/gateway.out"
no_needle "$token" "token" "$gw" "$work/gateway.out"
ok "still no content and no token on the gateway after the refused turn"

# 10. The plain install off again (the sidekick's data goes with the work dir).
bash "$repo/packaging/install.sh" --uninstall --home "$H" >/dev/null 2>&1 || true
echo "all seat gateway checks passed"
