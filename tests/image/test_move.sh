#!/usr/bin/env bash
# Move one sidekick from a container to a plain install on this machine and back:
#   tests/image/test_move.sh IMAGE      (Linux runner with a user systemd session)
# The memory written at the start must arrive at the end, and only the newest copy may be live.
# No pipefail: checks pipe into grep -q (see test_image.sh).
set -eu
IMG="${1:?usage: test_move.sh IMAGE}"
repo="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
H="$work/plain-home"
C1="sk-move-a-$$"; C2="sk-move-b-$$"
V1="$(docker volume create)"; V2="$(docker volume create)"
cleanup() {
    docker rm -f "$C1" "$C2" >/dev/null 2>&1 || true
    docker volume rm -f "$V1" "$V2" >/dev/null 2>&1 || true
    bash "$repo/packaging/install.sh" --uninstall --home "$H" >/dev/null 2>&1 || true
}
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }
wait_quiet() { local n="$1"; shift; for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done; return 1; }
status_of() { "$@" status | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance"]["status"])'; }
MEM=hermes/.skmemory/agents/sidekick/memory/long-term/move-test.json

# 1. A sidekick in a container, with one memory.
docker run -d --name "$C1" -v "$V1:/opt/data" "$IMG" >/dev/null
wait_quiet 180 sh -c "docker logs '$C1' 2>&1 | grep -q 'No sidekick here yet'" || fail "container A never came up"
phrase="$(docker exec "$C1" sksidekick init | grep -oE '([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}' | head -1)"
[ -n "$phrase" ] || fail "no phrase from init"
docker exec -u hermes "$C1" sh -c "mkdir -p \"\$(dirname /opt/data/sidekick/$MEM)\" && echo '{\"text\": \"the move test memory\"}' > /opt/data/sidekick/$MEM"
ok "container A holds a sidekick and a memory"

# 2. Container to plain install.
docker exec "$C1" sksidekick move-out /opt/data/move-1.sksk >/dev/null
docker cp "$C1:/opt/data/move-1.sksk" "$work/move-1.sksk"
bash "$repo/packaging/install.sh" --home "$H" --no-init >/dev/null
printf '%s\n' "$phrase" | "$HOME/.local/bin/sksidekick" --home "$H" import "$work/move-1.sksk" --phrase-stdin >/dev/null
grep -q "the move test memory" "$H/$MEM" || fail "the memory did not arrive in the plain install"
wait_quiet 90 pgrep -f "sksidekick serve" || fail "the plain install did not start serving after the import"
docker exec "$C1" sksidekick retire --yes >/dev/null || fail "container A could not retire"
[ "$(status_of "$HOME/.local/bin/sksidekick" --home "$H")" = live ] || fail "the plain copy is not live"
ok "container to plain install: memory arrived, plain copy live, container copy retired"

# 3. Plain install back to a new container.
"$HOME/.local/bin/sksidekick" --home "$H" move-out "$work/move-2.sksk" >/dev/null
docker run -d --name "$C2" -v "$V2:/opt/data" "$IMG" >/dev/null
wait_quiet 180 sh -c "docker logs '$C2' 2>&1 | grep -q 'No sidekick here yet'" || fail "container B never came up"
docker cp "$work/move-2.sksk" "$C2:/opt/data/move-2.sksk"
docker exec "$C2" chown hermes:hermes /opt/data/move-2.sksk
printf '%s\n' "$phrase" | docker exec -i "$C2" sksidekick import /opt/data/move-2.sksk --phrase-stdin >/dev/null
docker exec "$C2" grep -q "the move test memory" "/opt/data/sidekick/$MEM" || fail "the memory did not arrive back in a container"
"$HOME/.local/bin/sksidekick" --home "$H" retire --yes >/dev/null || fail "the plain copy could not retire"
[ ! -e "$H" ] || fail "the retired plain copy left its home behind"
[ "$(status_of docker exec "$C2" sksidekick)" = live ] || fail "container B is not live"
ok "plain install to container: memory arrived, container B live, plain copy retired"
echo "all move checks passed"
