#!/bin/sh
# Container command (and the plain install's service). On first run it starts the setup page; otherwise
# waits for a sidekick (setup, init or import), gives it its own Hermes home inside the
# sidekick home, then runs `sksidekick serve`. A copy that is not live idles instead of exiting, so a
# restart policy never turns a moved-out copy into a crash loop.
set -eu
home="${SKSIDEKICK_HOME:?}"
# Where Hermes' installer or boot hook seeded config.yaml, .env and SOUL.md, and the skmemory.json to use.
# Exported: setup seeds the sidekick's Hermes home from them before its first write (sksidekick.setup.seed),
# and the same code seeds it below for a sidekick made by init or import.
export SIDEKICK_SEED="${SIDEKICK_SEED:-/opt/data}"
export SIDEKICK_SKMEMORY_JSON="${SIDEKICK_SKMEMORY_JSON:-/opt/sksidekick/skmemory.json}"
bin="${SIDEKICK_BIN:-/opt/sksidekick/bin}"  # sksidekick's own venv
said=""

# First run: the setup page (unless SKSIDEKICK_SETUP=off, as in the plain install's service, where
# install.sh runs setup itself). It binds every address inside the container; the image publishes the
# port on the host's loopback only. It exits 0 when setup finishes, or when `sksidekick init` / `import`
# makes the sidekick another way; then the loop below sees instance.json and serve starts. It refuses
# (non-zero) a home that is already set up, and then the wait loop below says what to do.
# Nothing is seeded before setup starts: a seeded file would make the home non-empty, and init and import
# refuse a non-empty home. Setup seeds at its first step instead.
# SKSIDEKICK_SETUP_PORT: the port setup listens on inside the container (default 8765, the port the image
# EXPOSEs and the docs publish). Change it only when 8765 is taken where this script runs (tests do this);
# the printed address then shows that port.
if [ ! -f "$home/state/instance.json" ] && [ "${SKSIDEKICK_SETUP:-on}" != off ]; then
    echo "No sidekick here yet. Open the setup page below in your browser; publish its port on this computer only: docker run -p 127.0.0.1:8765:8765 ...   (or run: docker exec -it <container> sksidekick init, or sksidekick import ARCHIVE --phrase-stdin)"
    said=1
    # Only a container gets here (the plain install's service has SKSIDEKICK_SETUP=off): say so to setup,
    # whose model and backup hints differ there. The image's Dockerfile sets it too, for docker exec.
    # SKSIDEKICK_INVITE: a path to an invite file mounted into the container (setup deletes it once installed, or
    # warns when it cannot, as on a read-only mount). A missing or bad file is one line in this log and setup starts without a seat.
    invite_args=""
    [ -z "${SKSIDEKICK_INVITE:-}" ] || invite_args=1
    SKSIDEKICK_IN_CONTAINER="${SKSIDEKICK_IN_CONTAINER:-1}" \
        "$bin/sksidekick" setup --host 0.0.0.0 --port "${SKSIDEKICK_SETUP_PORT:-8765}" \
        ${invite_args:+--invite "$SKSIDEKICK_INVITE"} &
    setup_pid=$!
    stopping=""
    # docker stop and systemd send SIGTERM here: pass it on so setup shreds an unconfirmed key, then leave.
    trap 'stopping=1; kill -TERM "$setup_pid" 2>/dev/null || true' TERM HUP INT
    setup_code=0
    while :; do
        wait "$setup_pid" && setup_code=0 || setup_code=$?
        kill -0 "$setup_pid" 2>/dev/null || break  # a trap only interrupted the wait: wait again
    done
    trap - TERM HUP INT
    [ -z "$stopping" ] || exit 143
    if [ "$setup_code" != 0 ]; then
        echo "Setup stopped (exit $setup_code)."
        said=""
    fi
fi

while [ ! -f "$home/state/instance.json" ]; do
    if [ -z "$said" ]; then
        if [ -n "${SIDEKICK_SETUP_CMD:-}" ]; then  # a plain install's service: setup runs in a terminal
            echo "No sidekick here yet. Run \"$SIDEKICK_SETUP_CMD\" in a terminal to set it up, or \"$SIDEKICK_SETUP_CMD\" --start-over to throw away a setup that stopped half way (or \"$bin/sksidekick\" --home \"$home\" import ARCHIVE --phrase-stdin when moving here)."
        else
            echo "No sidekick here yet. Run: docker exec -it <container> sksidekick init   (or sksidekick import ARCHIVE --phrase-stdin). If a setup stopped half way, first: docker exec -it <container> sksidekick setup --start-over"
        fi
        said=1
    fi
    sleep 5
done

# The sidekick's own Hermes home: seed files it lacks, skmemory.json, memory.provider skmemory.
"$bin/python" -m sksidekick.setup.seed "$home"
export HERMES_HOME="$home/hermes"

code=0
"$bin/sksidekick" serve || code=$?
if ! "$bin/python" -c 'import json, os, sys; s = json.load(open(os.path.join(sys.argv[1], "state", "instance.json")))["status"]; sys.exit(0 if s == "live" else 1)' "$home"; then
    if [ -n "${SIDEKICK_SETUP_CMD:-}" ]; then
        echo "This copy is not the live sidekick (moved out or retired). It stays stopped. When the new place works: \"$bin/sksidekick\" --home \"$home\" retire --yes"
    else
        echo "This copy is not the live sidekick (moved out or retired). It stays stopped. When the new place works: docker exec <container> sksidekick retire --yes"
    fi
    exec sleep infinity
fi
exit "$code"
