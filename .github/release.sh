#!/usr/bin/env bash
# Publishes a GitHub release of the Windows, Linux and macOS packages that the build workflows
# made from one commit (the Release workflow runs it; Actions -> Release -> Run workflow).
#
# usage: release.sh TAG [COMMIT] [--prerelease] [--dry-run]
#   COMMIT: default, the newest commit on the default branch that all three build workflows
#           built successfully (documentation-only commits don't start builds)
#   --dry-run: download and package everything into ./release-out, but don't publish
# Needs gh with a token that may read the repository's workflow runs and create releases.
set -euo pipefail

tag="" commit="" prerelease="" dry_run=""
for arg in "$@"; do
    case "$arg" in
        --prerelease) prerelease=--prerelease ;;
        --dry-run) dry_run=1 ;;
        -*) echo "unknown option $arg" >&2; exit 2 ;;
        *) if [ -z "$tag" ]; then tag=$arg; elif [ -z "$commit" ]; then commit=$arg; fi ;;
    esac
done
if ! [[ "$tag" =~ ^v[0-9]+(\.[0-9]+)*(-[0-9A-Za-z.]+)?$ ]]; then
    echo "usage: $0 TAG [COMMIT] [--prerelease] [--dry-run]   (TAG like v0.1.0)" >&2
    exit 2
fi
repo=${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}

# the newest successful run of a workflow for a commit, if any
run_for() {
    gh run list -R "$repo" --workflow "$1" --commit "$2" --status success --limit 1 \
        --json databaseId -q '.[0].databaseId // empty'
}

if [ -z "$commit" ]; then
    for c in $(git log --format=%H -n 60 HEAD); do
        if [ -n "$(run_for windows.yml "$c")" ] && [ -n "$(run_for linux.yml "$c")" ] &&
            [ -n "$(run_for macos.yml "$c")" ]; then
            commit=$c
            break
        fi
    done
    [ -n "$commit" ] || { echo "no recent commit has successful Windows, Linux and macOS builds" >&2; exit 1; }
fi
commit=$(git rev-parse "$commit^{commit}")
echo "releasing $tag from $commit"

out=release-out
rm -rf "$out" && mkdir -p "$out/dl"
for system in windows linux macos; do
    id=$(run_for "$system.yml" "$commit")
    [ -n "$id" ] || { echo "no successful $system build of $commit" >&2; exit 1; }
    # (artifacts are kept for 14 days: an older commit needs its builds run again)
    gh run download "$id" -R "$repo" -n "mh4u-port-$system" -D "$out/dl/$system"
done

# every package has the port's app, which the release notes start the game with
linux="$out/dl/linux/mh4u-port-linux"
for app in "$out/dl/windows/mh4u-port-windows/mh4u.exe" "$linux/mh4u.AppImage"; do
    [ -f "$app" ] || { echo "the packages of $commit have no ${app##*/}: release a newer commit" >&2; exit 1; }
done
# (grep reads the whole list: with -q, unzip could die of a closed pipe and fail the check)
unzip -Z1 "$out/dl/macos/mh4u-port-macos.zip" | grep -x "mh4u-port-macos/MH4U.app/Contents/MacOS/mh4u" >/dev/null ||
    { echo "the packages of $commit have no MH4U.app: release a newer commit" >&2; exit 1; }

# Windows: the folder as a zip; Linux: a tarball, which keeps the AppImages and the scripts
# executable; macOS: the workflow's own zip (made with ditto, which keeps the apps intact)
(cd "$out/dl/windows" && zip -q -r -9 ../../mh4u-port-windows.zip mh4u-port-windows)
chmod +x "$linux"/*.AppImage
find "$linux" -name '*.sh' -exec chmod +x {} +
tar czf "$out/mh4u-port-linux.tar.gz" -C "$out/dl/linux" mh4u-port-linux
cp "$out/dl/macos/mh4u-port-macos.zip" "$out/"

azahar=$(git show "$commit:port/azahar/COMMIT" | tr -d '[:space:]')
sed -e "s|@COMMIT@|$commit|g" -e "s|@AZAHAR@|$azahar|g" -e "s|@REPO@|https://github.com/$repo|g" \
    .github/release-notes.md >"$out/notes.md"

assets=("$out/mh4u-port-windows.zip" "$out/mh4u-port-linux.tar.gz" "$out/mh4u-port-macos.zip")
ls -l "${assets[@]}"
if [ -n "$dry_run" ]; then
    echo "dry run: not publishing $tag"
    exit 0
fi
gh release create "$tag" "${assets[@]}" -R "$repo" --target "$commit" \
    --title "MH4U PC port ${tag#v}" --notes-file "$out/notes.md" $prerelease
