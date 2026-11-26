#!/bin/sh
# Leak search for the recovery phrase, the same on GNU and BSD (macOS) and inside the image:
#   printf '%s\n' PATTERN... | sh tests/leak_search.sh PATH...
# Patterns come on stdin, one per line (never on a command line, where ps would show them). Prints every
# regular file under PATH that holds one, and nothing when the search is clean; exit 0 either way, so the
# caller decides on the output. Exit 2 only when the search itself failed: no patterns, or find could not
# read a directory (a file or directory that vanished during the walk is not a failure: serve may be
# running). grep's own exit status is not used: BSD grep exits 2 on a socket or an unreadable file even
# with -s, and xargs folds "no match" into 123.
set -eu
[ $# -gt 0 ] || { echo "usage: leak_search.sh PATH... (patterns on stdin)" >&2; exit 2; }
for p in "$@"; do
    [ -e "$p" ] || { echo "leak_search.sh: nothing at $p to search" >&2; exit 2; }
done
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pf="$tmp/patterns"
(umask 077; cat > "$pf")
[ -s "$pf" ] || { echo "leak_search.sh: no patterns on stdin" >&2; exit 2; }
rc=0
find "$@" -type f ! -path "$tmp/*" -print0 2>"$tmp/find.err" > "$tmp/list" || rc=$?
if [ "$rc" != 0 ] && grep -v 'No such file or directory' "$tmp/find.err" | grep -q .; then
    echo "leak_search.sh: could not list the files to search:" >&2
    head -5 "$tmp/find.err" >&2
    exit 2
fi
# /dev/null first: with an empty list grep still gets a file, so it never reads stdin.
xargs -0 grep -lsF -f "$pf" -- /dev/null < "$tmp/list" || true
