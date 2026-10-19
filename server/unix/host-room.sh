#!/bin/sh
# Hosts a network room for Monster Hunter 4 Ultimate's online play with Azahar's dedicated room
# server (azahar-room). Players join with Multiplayer > Direct Connect to Room, or find the room
# in Multiplayer > Browse Public Rooms when it is listed in a lobby. All the game traffic goes
# through the room, over UDP (port 24872 unless you change it): forward that port on your router
# to this computer. docs/SERVER.md in the repository has the details.
#
# azahar-room (or Azahar's azahar-room.AppImage): $AZAHAR_ROOM, or next to this script, in the
# folder above it, or on the PATH (the port's Windows package has it; on Linux, build it with
# Azahar or use server/docker).
# Settings: host-room.ini in ~/.local/share/mh4u-server (macOS: ~/Library/Application Support/
# MH4U Server; or $MH4U_SERVER_DIR), made with the defaults on the first run. key=value
# arguments override it for one run:
#   ./host-room.sh name="Luis's hall" password=secret
# Close the room with q and Enter: it then leaves the lobby and saves its ban list.
set -u
umask 077  # the settings hold the room's token and password, the ban list players' addresses
here=$(cd "$(dirname "$0")" && pwd)

fail() {
    echo "$*"
    exit 1
}

room=${AZAHAR_ROOM:-}
if [ -z "$room" ]; then
    for candidate in "$here/azahar-room" "$here/azahar-room.AppImage" "$here/../azahar-room" \
        "$here/../azahar-room.AppImage"; do
        if [ -x "$candidate" ]; then
            room=$candidate
            break
        fi
    done
fi
[ -n "$room" ] || room=$(command -v azahar-room 2>/dev/null) || true
[ -n "$room" ] && [ -x "$room" ] ||
    fail "Can't find azahar-room: put it next to this script or on the PATH, or set AZAHAR_ROOM"

if [ -n "${MH4U_SERVER_DIR:-}" ]; then
    folder=$MH4U_SERVER_DIR
elif [ "$(uname)" = Darwin ]; then
    folder="$HOME/Library/Application Support/MH4U Server"
else
    folder="${XDG_DATA_HOME:-$HOME/.local/share}/mh4u-server"
fi
mkdir -p "$folder" || fail "Can't make $folder"
ini="$folder/host-room.ini"
if [ ! -f "$ini" ]; then
    cat >"$ini" <<'INI'
# Settings for host-room.sh: an MH4U room on Azahar's dedicated room server.
# One key = value per line; delete a line to go back to its default.
name = MH4U Gathering Hall
description =
# the room's UDP port: forward it on your router to this computer
port = 24872
max_members = 16
# empty: no password
password =
# To list the room in a lobby (Multiplayer > Browse Public Rooms): the lobby's address
# (http://...:24872) and the room's name and token there. In an open lobby the first
# use claims the name; use your own Azahar username to be the room's moderator.
lobby_url =
username =
token =
# the room's bans, kept between runs (in this folder unless it's a full path)
ban_list = room-ban-list.txt
INI
    echo "Made $ini with the defaults: edit it to change the room."
fi
chmod 600 "$ini"  # (one made by an older version of this script was readable by all)

name="MH4U Gathering Hall" description="" port=24872 max_members=16 password=""
lobby_url="" username="" token="" ban_list="room-ban-list.txt"
trim() {
    printf '%s' "$1" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}
setting() {  # setting "key = value"
    key=$(trim "${1%%=*}")
    value=$(trim "${1#*=}")
    case "$key" in
        name) name=$value ;;
        description) description=$value ;;
        port) port=$value ;;
        max_members) max_members=$value ;;
        password) password=$value ;;
        lobby_url) lobby_url=$value ;;
        username) username=$value ;;
        token) token=$value ;;
        ban_list) ban_list=$value ;;
        *) echo "Unknown setting: $key" ;;
    esac
}
while IFS= read -r line || [ -n "$line" ]; do
    line=$(trim "$line")
    case "$line" in
        '' | '#'*) ;;
        *=*) setting "$line" ;;
    esac
done <"$ini"
for arg in "$@"; do
    case "$arg" in
        *=*) setting "$arg" ;;
        *) fail "Not a setting: $arg (key=value, e.g. port=24873)" ;;
    esac
done

[ -n "$name" ] || fail "The room needs a name (name = ...)"
case "$port" in '' | *[!0-9]*) port=0 ;; esac
[ "$port" -ge 1 ] && [ "$port" -le 65535 ] || fail "port must be a number from 1 to 65535"
case "$max_members" in '' | *[!0-9]*) max_members=0 ;; esac
[ "$max_members" -ge 2 ] && [ "$max_members" -le 254 ] ||
    fail "max_members must be a number from 2 to 254"
case "$ban_list" in /*) ;; *) ban_list="$folder/$ban_list" ;; esac
[ -f "$ban_list" ] || printf 'CitraRoom-BanList-1\n' >"$ban_list"  # an empty one

set -- --room-name "$name" --port "$port" --max_members "$max_members" \
    --preferred-app "Monster Hunter 4 Ultimate" --preferred-app-id 0004000000126300 \
    --ban-list-file "$ban_list"
if [ -n "$description" ]; then
    set -- "$@" --room-description "$description"
fi
if [ -n "$password" ]; then
    set -- "$@" --password "$password"
fi

echo "Room '$name': UDP port $port, up to $max_members players, $([ -n "$password" ] && echo "with a password" || echo "no password")."
if [ -n "$lobby_url" ] && [ -n "$username" ] && [ -n "$token" ]; then
    set -- "$@" --web-api-url "$lobby_url" --username "$username" --token "$token"
    echo "Listed in the lobby at $lobby_url as $username: players find it in"
    echo "Multiplayer > Browse Public Rooms (with that lobby as their Network Web API URL)."
else
    [ -z "$lobby_url" ] || echo "(lobby_url needs username and token too, to list the room)"
    echo "Unlisted: players use Multiplayer > Direct Connect to Room, this computer's public"
    echo "address (or its address in a virtual network such as Tailscale) and port $port."
fi
addresses=$( (hostname -I || ipconfig getifaddr en0) 2>/dev/null | tr -s ' \n' '  ' |
    sed 's/ *$//') || true
[ -z "$addresses" ] ||
    echo "This computer on its network: $addresses (forward UDP $port on the router to it)."
echo "Settings: $ini"
echo
exec "$room" "$@"
