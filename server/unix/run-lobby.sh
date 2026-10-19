#!/bin/sh
# Runs the MH4U lobby server (mh4u_lobby.py): the room list Azahar shows in Multiplayer > Browse
# Public Rooms, and its check of names. Needs Python 3.10 or newer. Its names and keys stay in
# ~/.local/share/mh4u-server/lobby (macOS: ~/Library/Application Support/MH4U Server/lobby; or
# $MH4U_SERVER_DIR/lobby). Players use http://<this computer's address>:24872 as their Network
# Web API URL: forward TCP 24872 on the router to this computer. See docs/SERVER.md.
# Options and commands go to mh4u_lobby.py, e.g.
#   ./run-lobby.sh --mode invite
#   ./run-lobby.sh add-user "Hunter One"      (prints that name's token)
#   ./run-lobby.sh list-users
set -u
here=$(cd "$(dirname "$0")" && pwd)

lobby="$here/mh4u_lobby.py"
[ -f "$lobby" ] || lobby="$here/../lobby/mh4u_lobby.py"
if [ ! -f "$lobby" ]; then
    echo "Can't find mh4u_lobby.py next to this script or in ../lobby"
    exit 1
fi

python=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        python=$candidate
        break
    fi
done
if [ -z "$python" ]; then
    echo "Python 3.10 or newer is needed: from your package manager, python.org or Homebrew."
    exit 1
fi

if [ -n "${MH4U_SERVER_DIR:-}" ]; then
    folder=$MH4U_SERVER_DIR
elif [ "$(uname)" = Darwin ]; then
    folder="$HOME/Library/Application Support/MH4U Server"
else
    folder="${XDG_DATA_HOME:-$HOME/.local/share}/mh4u-server"
fi
exec "$python" "$lobby" --data "$folder/lobby" "$@"
