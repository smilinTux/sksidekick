#!/bin/sh
# sidekick-invite: makes a mounted invite readable by setup. The image runs it as root at container start
# (/etc/cont-init.d/03-sksidekick-invite), before the drop to the hermes user that runs setup.
#
# An operator's invite is 0600 and owned by the operator, so a read only bind mount of it (docs/SEATS.md)
# is unreadable to the hermes user. When the hermes user cannot read SKSIDEKICK_INVITE, this copies it into
# the data volume as invite.json (owned by hermes, mode 0600) and points SKSIDEKICK_INVITE at the copy for
# the container command. Setup deletes the file it was given once the shared option is saved, so it deletes
# the copy; the mounted original is left alone. A file hermes can already read is used where it is (an
# invite copied into the volume by hand keeps working). Nothing happens once the sidekick is set up.
# It never prints the invite, and never stops the container: a problem is one line in the log, and setup
# then says it could not read the invite.
#
# Knobs for the tests (the defaults are the image's): SIDEKICK_DATA (the data volume), SIDEKICK_ENVDIR
# (s6's container environment), SIDEKICK_INVITE_OWNER (owner of the copy), SIDEKICK_AS_HERMES (the
# command prefix that runs a check as the hermes user).
set -eu
src="${SKSIDEKICK_INVITE:-}"
[ -n "$src" ] || exit 0
home="${SKSIDEKICK_HOME:-/opt/data/sidekick}"
data="${SIDEKICK_DATA:-/opt/data}"
dest="$data/invite.json"
envdir="${SIDEKICK_ENVDIR:-/run/s6/container_environment}"
owner="${SIDEKICK_INVITE_OWNER:-hermes:hermes}"
as_hermes="${SIDEKICK_AS_HERMES:-s6-setuidgid hermes}"
say() { echo "[sksidekick] $*"; }

# Already set up: sidekick-start says the invite was not used. Do not put a secret on the volume for nothing.
[ ! -f "$home/state/instance.json" ] || exit 0
# Missing, or not a plain file: setup says it could not read it.
[ -f "$src" ] || exit 0
# shellcheck disable=SC2086  # as_hermes is a command prefix
if $as_hermes test -r "$src" 2>/dev/null; then
    exit 0
fi
if [ -L "$src" ]; then
    say "the invite $src is a symbolic link; it was not copied (mount the file itself)"
    exit 0
fi
case "$src" in
    "$data"/*)
        # The volume belongs to the hermes user: a file there it cannot read is not root's to hand over.
        say "the invite $src is in the data volume but the hermes user cannot read it; chown it to hermes"
        exit 0 ;;
esac
size="$(wc -c < "$src" | tr -d ' ')"
if [ "$size" -gt 65536 ]; then
    say "the invite $src is too large to be an invite; it was not copied"
    exit 0
fi

tmp=""
trap '[ -z "$tmp" ] || rm -f "$tmp"' EXIT
if ! tmp="$(mktemp "$data/.invite.XXXXXX")"; then
    tmp=""
    say "could not copy the invite into $data; setup will not be able to read it"
    exit 0
fi
if ! { cat "$src" > "$tmp" && chown "$owner" "$tmp" && chmod 0600 "$tmp"; }; then
    say "could not copy the invite into $data; setup will not be able to read it"
    exit 0
fi
if [ -L "$dest" ]; then
    rm -f "$dest"
elif [ -d "$dest" ]; then
    say "$dest is a directory; the invite was not copied"
    exit 0
fi
mv -f "$tmp" "$dest"
tmp=""
if [ -d "$envdir" ]; then
    printf '%s' "$dest" > "$envdir/SKSIDEKICK_INVITE"
fi
say "the invite is not readable by the hermes user, so setup uses a private copy at $dest (deleted once the shared option is saved)"
