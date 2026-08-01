#!/usr/bin/env bash
#
# Cut a release: bump the manifest, commit it, tag that commit, push, and check
# that the updater page will actually show what you asked for.
#
#   updater/tag-release.sh v1.11-dannyb              # derive 1.11.0, bump build, commit, tag, push
#   updater/tag-release.sh v1.11-dannyb --version 1.11.2 --build 80
#   updater/tag-release.sh v1.11-dannyb --tag-only   # manifest already committed; just tag
#   updater/tag-release.sh v1.11-dannyb --no-push    # local only
#   updater/tag-release.sh --check                   # what shows today, change nothing
#
# The updater page's version line is TWO independent things:
#
#   Current: v<metadata.versions.server from updater/updater_manifest.json>
#            (<git describe --tags --always>)
#   Remote:  <last entry of `git tag --sort=v:refname --merged <ref>`>
#
# So a tag alone moves only half of it, and `git describe` appends -<n>-g<sha>
# unless the tag is ON the commit you are running -- which is why the manifest
# bump is committed FIRST and the tag goes on that commit.
#
# Two traps this checks for you:
#
#   * `--sort=v:refname` is a VERSION sort, not creation order. A tag whose name
#     sorts below an existing one is created, pushed, and silently ignored --
#     v1.11-dannyb sorts BELOW v1.11.0-dev17, because git compares 1.11 against
#     1.11.0 long before it reaches the suffix.
#   * common/common.py's semantic_ver_to_list runs int() over each dotted part of
#     the manifest version, so it must be PURELY NUMERIC. A "1.11-dannyb" in
#     there raises ValueError inside settings migration on the next boot.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MANIFEST="updater/updater_manifest.json"
TAG=""
VERSION=""
BUILD=""
PUSH=1
COMMIT=1
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--check) CHECK_ONLY=1 ;;
	--no-push) PUSH=0 ;;
	--tag-only) COMMIT=0 ;;
	--version)
		VERSION="${2:-}"
		shift
		;;
	--build)
		BUILD="${2:-}"
		shift
		;;
	-h | --help)
		sed -n '2,30p' "$0" | sed 's/^# \?//'
		exit 0
		;;
	-*)
		echo "unknown option: $1" >&2
		exit 2
		;;
	*) TAG="$1" ;;
	esac
	shift
done

json() { python3 -c "import json,sys; print(json.load(open('$MANIFEST'))$1)"; }

# The ref the updater measures "Remote:" against: origin/<branch>, or HEAD when
# the checkout is detached -- the same choice updater.py's get_remote_version makes.
measured_against() {
	local branch
	if branch="$(git symbolic-ref --quiet --short HEAD)" &&
		git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
		echo "origin/$branch"
	else
		echo "HEAD"
	fi
}

report() {
	echo
	echo "Measured against : $(measured_against)"
	echo "Current: will show v$(json "['metadata']['versions']['server']") ($(git describe --tags --always))"
	echo "Remote:  will show $(git tag --sort=v:refname --merged "$(measured_against)" | tail -n 1)"
}

if [[ "$CHECK_ONLY" == 1 ]]; then
	report
	exit 0
fi

if [[ -z "$TAG" ]]; then
	echo "usage: $0 <tag> [--version X.Y.Z] [--build N] [--tag-only] [--no-push]   (or --check)" >&2
	exit 2
fi

if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
	echo "Tag $TAG already exists. Delete it first:" >&2
	echo "  git tag -d $TAG && git push origin :refs/tags/$TAG" >&2
	exit 1
fi

# Derived by stripping the leading v and anything from the first '-', then
# padding to three parts: v1.11-dannyb -> 1.11.0. Override with --version.
if [[ -z "$VERSION" ]]; then
	VERSION="${TAG#v}"
	VERSION="${VERSION%%-*}"
	while [[ "$(tr -cd '.' <<<"$VERSION" | wc -c)" -lt 2 ]]; do VERSION="$VERSION.0"; done
fi
if [[ ! "$VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
	echo "Refusing: manifest version '$VERSION' is not purely numeric." >&2
	echo "semantic_ver_to_list() runs int() over each dotted part, so this would" >&2
	echo "raise inside settings migration on the next boot. Pass --version X.Y.Z." >&2
	exit 1
fi

[[ -n "$BUILD" ]] || BUILD=$(($(json "['metadata']['versions']['build']") + 1))

if [[ "$COMMIT" == 1 ]]; then
	python3 - "$MANIFEST" "$VERSION" "$BUILD" <<-'PY'
		import json, sys
		path, version, build = sys.argv[1], sys.argv[2], int(sys.argv[3])
		with open(path) as handle:
		    manifest = json.load(handle)
		manifest["metadata"]["versions"]["server"] = version
		manifest["metadata"]["versions"]["build"] = build
		with open(path, "w") as handle:
		    json.dump(manifest, handle, indent=2)
		    handle.write("\n")
	PY
	echo "Set $MANIFEST to server $VERSION, build $BUILD"

	if git diff --quiet -- "$MANIFEST"; then
		echo "($MANIFEST was already at those values; nothing to commit)"
	else
		# Only the manifest: this repo often has other work in progress.
		git add -- "$MANIFEST"
		git commit -q -m "chore(release): $TAG (server $VERSION, build $BUILD)" -- "$MANIFEST"
		echo "Committed $(git rev-parse --short HEAD)"
	fi
fi

# Checked rather than assumed, so --tag-only cannot quietly tag a commit whose
# manifest says something else.
HEAD_VERSION="$(git show "HEAD:$MANIFEST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["metadata"]["versions"]["server"])')"
if [[ "$HEAD_VERSION" != "$VERSION" ]]; then
	echo "Refusing: HEAD's manifest says server $HEAD_VERSION, not $VERSION." >&2
	echo "Commit the manifest bump before tagging, or drop --tag-only." >&2
	exit 1
fi

# Annotated, not lightweight: `git describe`, which is what the page's "Current:"
# field runs, prefers annotated tags and ignores lightweight ones unless asked.
git tag -a "$TAG" -m "$TAG"
echo "Tagged $(git rev-parse --short HEAD) as $TAG"

if [[ "$PUSH" == 1 ]]; then
	branch="$(git symbolic-ref --quiet --short HEAD || true)"
	if [[ -n "$branch" ]]; then
		git push origin "$branch"
	else
		echo "Detached HEAD: not pushing the commit, only the tag."
	fi
	git push origin "refs/tags/$TAG"
	echo "Pushed $TAG to origin"
else
	echo "Not pushed (--no-push):"
	echo "  git push origin HEAD refs/tags/$TAG"
fi

report

problems=0
described="$(git describe --tags --always)"
if [[ "$described" != "$TAG" ]]; then
	echo
	echo "!! git describe says '$described', not '$TAG' -- the tag is not on the"
	echo "!! commit you are running, so Current: will carry a -N-g<sha> suffix."
	problems=1
fi
shown="$(git tag --sort=v:refname --merged "$(measured_against)" | tail -n 1)"
if [[ "$shown" != "$TAG" ]]; then
	echo
	echo "!! Remote: will keep showing $shown -- it version-sorts above $TAG."
	echo "!! Compare with:  git tag --sort=v:refname | tail -5"
	problems=1
fi
exit "$problems"
