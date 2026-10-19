#!/usr/bin/env bash
# Builds the game's code library for Android (arm64-v8a) on a Linux or macOS PC, from your own
# dump, with the Android NDK's compiler. Nothing here runs on the phone: Android has no
# compiler, so the library is made on the PC and copied over.
#
#   ANDROID_NDK_HOME=~/Android/Sdk/ndk/27.0.12077973 \
#     port/android/build-code-library.sh "MH4U v1.3.0 update.cia" [O1|O2|O0]
#
# Output: build/android/recomp/0004000000126300.so, to be copied to the phone into
#   the app (Setup > "Choose the library"), or the app's files folder: Android/data/org.mh4u.port/files/recomp/
# (Azahar's app copies it into its own storage when the game starts: shared storage is noexec.)
set -euo pipefail

DUMP="${1:?usage: $0 <update .cia or dump folder> [O1|O2|O0]}"
OPT="${2:-O1}"
API=29 # Android 10, the lowest version the app supports
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the NDK folder (r26 or newer)}"
case "$(uname -s)" in
    Linux) HOST_TAG=linux-x86_64 ;;
    Darwin) HOST_TAG=darwin-x86_64 ;; # the NDK's prebuilt toolchain is x86_64 on Apple silicon too
    *) echo "Use Linux or macOS (or WSL) for this." >&2; exit 1 ;;
esac
CC="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/$HOST_TAG/bin/aarch64-linux-android${API}-clang"
[ -x "$CC" ] || { echo "no compiler at $CC" >&2; exit 1; }

cd "$ROOT"
# Extract, check the version, recompile, compile with the NDK compiler. Extra arguments go to
# build_game.py (e.g. --mods DIR to build in the code mods of a folder copied from the phone).
python3 tools/build_game.py --dump "$DUMP" --cc "$CC" --opt "$OPT" --lib-ext so --out build/android "${@:3}"

LIB="$(ls build/android/recomp/*.so | head -n 1)"
echo
echo "Done: $LIB"
echo "Copy it to the phone and choose it in the app: Setup > Choose the library. Or, with adb:"
echo "  adb push $LIB /sdcard/Android/data/org.mh4u.port/files/recomp/$(basename "$LIB")"
