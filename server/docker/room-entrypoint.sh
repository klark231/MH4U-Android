#!/bin/sh
# Starts Azahar's dedicated room (azahar-room) with MH4U's settings from environment variables,
# in the room's container (Dockerfile.room, compose.yaml). docker stop is passed on as the q and
# Enter that the room waits for on its console, so that it leaves the lobby at once and saves its
# ban list (ban-list.txt in the working folder, /data) before it exits.
#
#   ROOM_NAME         the room's name (MH4U Gathering Hall)
#   ROOM_DESCRIPTION  a line about it, shown in the lobby
#   ROOM_PORT         its UDP port (24872): publish the same number on the host
#   ROOM_PASSWORD     a password, if any
#   ROOM_MAX_MEMBERS  at most this many players (16; 2 to 254)
#   LOBBY_URL         a lobby to announce itself to (http://host:24872), with
#   LOBBY_USERNAME    the room's name there and
#   LOBBY_TOKEN       its token there; without all three the room is unlisted (Direct Connect)
# Arguments go to azahar-room after these.
set -u
umask 077  # the ban list names players and their addresses
stop=""
trap 'stop=1' TERM INT  # a stop before the room has its console: passed on below
ban_list="$PWD/ban-list.txt"
[ -f "$ban_list" ] || printf 'CitraRoom-BanList-1\n' >"$ban_list"  # an empty one

set -- --room-name "${ROOM_NAME:-MH4U Gathering Hall}" --port "${ROOM_PORT:-24872}" \
    --max_members "${ROOM_MAX_MEMBERS:-16}" \
    --preferred-app "Monster Hunter 4 Ultimate" --preferred-app-id 0004000000126300 \
    --ban-list-file "$ban_list" "$@"
if [ -n "${ROOM_DESCRIPTION:-}" ]; then
    set -- "$@" --room-description "$ROOM_DESCRIPTION"
fi
if [ -n "${ROOM_PASSWORD:-}" ]; then
    set -- "$@" --password "$ROOM_PASSWORD"
fi
if [ -n "${LOBBY_URL:-}" ] && [ -n "${LOBBY_USERNAME:-}" ] && [ -n "${LOBBY_TOKEN:-}" ]; then
    set -- "$@" --web-api-url "$LOBBY_URL" --username "$LOBBY_USERNAME" --token "$LOBBY_TOKEN"
    echo "Announcing the room to $LOBBY_URL as $LOBBY_USERNAME"
else
    echo "No LOBBY_URL, LOBBY_USERNAME and LOBBY_TOKEN: an unlisted room (Direct Connect)"
fi

# the room reads its console: a pipe that stays open, and q when it should close
console=$(mktemp -u "${TMPDIR:-/tmp}/room-console.XXXXXX")
mkfifo "$console"
azahar-room "$@" <"$console" &
room=$!
exec 3>"$console"
rm -f "$console"
trap 'echo q >&3' TERM INT
[ -z "$stop" ] || echo q >&3
echo "Room started (pid $room): docker stop closes it"
wait "$room"
status=$?
# A trapped signal ends the wait early (128 + the signal) while the room is still closing, or
# just after it exited and the shell collected it (so kill -0 can't tell): wait again for the
# room's own status. 127 means the shell has no such child any more: keep what it said.
while [ "$status" -gt 128 ]; do
    wait "$room"
    again=$?
    [ "$again" -eq 127 ] && break
    status=$again
done
exit "$status"
