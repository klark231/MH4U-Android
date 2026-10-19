#!/bin/sh
# Builds the game's code library from YOUR Monster Hunter 4 Ultimate USA v1.3.0 update (.cia)
# and installs it where this Azahar looks for it (Linux: ~/.local/share/azahar-emu/recomp,
# macOS: ~/Library/Application Support/Azahar/recomp). On a computer without clang the first run
# downloads Zig's C compiler (about 80 MB). Everything it makes from the game stays on this
# computer. Part of the MH4U PC port's Linux and macOS packages (see README-mh4u.txt).
#
# usage: ./build-code-library.sh [path/to/update.cia]   (without a path: a file dialog, if any)
set -u
here=$(cd "$(dirname "$0")" && pwd)
tool="$here/mh4u-tools/tools/build_game.py"

finish() {
    # keep a window opened by a double-click until the player has read it
    if [ -t 0 ] && [ -z "${MH4U_NO_PAUSE:-}" ]; then
        printf 'Press Enter to close '
        read -r _
    fi
    exit "$1"
}
fail() {
    echo "$*"
    finish 1
}

[ -f "$tool" ] || fail "Can't find $tool (keep this script next to the mh4u-tools folder)"

python=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        python=$candidate
        break
    fi
done
[ -n "$python" ] || fail "Python 3.10 or newer is needed: from your package manager, python.org or Homebrew."

cia=${1:-}
if [ -z "$cia" ]; then
    title="Your decrypted MH4U USA v1.3.0 update (.cia)"
    if [ "$(uname)" = Darwin ]; then
        cia=$(osascript -e "POSIX path of (choose file with prompt \"$title\")" 2>/dev/null) ||
            fail "No file chosen."
    elif command -v zenity >/dev/null 2>&1; then
        cia=$(zenity --file-selection --title="$title" --file-filter="*.cia" 2>/dev/null) ||
            fail "No file chosen."
    elif command -v kdialog >/dev/null 2>&1; then
        cia=$(kdialog --title "$title" --getopenfilename "$HOME" "*.cia" 2>/dev/null) ||
            fail "No file chosen."
    else
        fail "usage: $0 path/to/update.cia"
    fi
fi
[ -f "$cia" ] || fail "Can't find $cia"

echo "Building the code library from $cia"
"$python" "$tool" --dump "$cia" --install --fetch-compiler ||
    fail "Something went wrong: see the text above."

# The port's options, if there are none yet (Tools > MH4U Port Options... in Azahar has them all)
if [ "$(uname)" = Darwin ]; then
    user_dir="$HOME/Library/Application Support/Azahar"
else
    user_dir="${XDG_DATA_HOME:-$HOME/.local/share}/azahar-emu"
fi
ini="$user_dir/mh4u.ini"
if [ ! -f "$ini" ]; then
    mkdir -p "$user_dir"
    cat >"$ini" <<'INI'
# MH4U PC port options (Tools > MH4U Port Options... in Azahar). Delete a line to go back to the game's own.
fps = 60
widescreen = 16:9
wide_render = 1
# the touch screen in a corner of View > Screen Layout > Custom Layout (corner, height %, opacity %)
touch_overlay = top-right 30 85
# no dither grid over an upscaled picture
dither = off
# no glow over bright parts, and sharp cutscenes (emulation gets their focus blur wrong)
bloom = off
depth_of_field = off
INI
    echo "Made $ini (60 fps, 16:9, the touch screen in a corner)"
fi

echo
echo "Done. In the port's Azahar:"
echo "  1. File > Install CIA... > your v1.3.0 update (.cia)"
echo "  2. File > Load File... > your decrypted game (.3ds / .cci / .cxi)"
echo "  3. View > Screen Layout > Custom Layout: the game fills the window, the touch screen in a corner"
echo "  Azahar says so at the start if the game runs without the port's code library."
finish 0
