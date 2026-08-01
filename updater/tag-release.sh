#!/usr/bin/env bash
#
# Tag a release, and say whether the updater page will actually show it.
#
#   updater/tag-release.sh v1.11-dannyb            # tag HEAD, check, push
#   updater/tag-release.sh v1.11-dannyb <commit>   # tag a specific commit
#   updater/tag-release.sh --no-push v1.11-dannyb  # local only
#   updater/tag-release.sh --check                 # what shows today, no changes
#
# The check is the point. "Remote:" on the updater page is the LAST entry of
#
#     git tag --sort=v:refname --merged <origin/branch, or HEAD when detached>
#
# and that sort is git's VERSION sort, not creation order -- so a new tag whose
# name sorts below an existing one is created, pushed, and silently ignored.
# v1.11-dannyb sorts BELOW v1.11.0-dev17, because git compares 1.11 against
# 1.11.0 before it ever reaches the suffix.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PUSH=1
CHECK_ONLY=0
TAG=""
TARGET="HEAD"

while [[ $# -gt 0 ]]; do
	case "$1" in
	--no-push) PUSH=0 ;;
	--check) CHECK_ONLY=1 ;;
	-h | --help)
		sed -n '2,20p' "$0" | sed 's/^# \?//'
		exit 0
		;;
	-*)
		echo "unknown option: $1" >&2
		exit 2
		;;
	*)
		if [[ -z "$TAG" ]]; then TAG="$1"; else TARGET="$1"; fi
		;;
	esac
	shift
done

# The ref the updater measures against: origin/<branch>, or HEAD when the
# checkout is detached -- the same choice updater.py's get_remote_version makes.
measured_against() {
	local branch
	if branch="$(git symbolic-ref --quiet --short HEAD)"; then
		if git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
			echo "origin/$branch"
			return
		fi
		echo "HEAD" # on a branch with no remote counterpart yet
		return
	fi
	echo "HEAD"
}

displayed_version() {
	git tag --sort=v:refname --merged "$(measured_against)" | tail -n 1
}

report() {
	local ref
	ref="$(measured_against)"
	echo
	echo "Measured against : $ref"
	echo "Will display as  : $(displayed_version)"
}

if [[ "$CHECK_ONLY" == 1 ]]; then
	report
	exit 0
fi

if [[ -z "$TAG" ]]; then
	echo "usage: $0 [--no-push] <tag> [commit]   (or --check)" >&2
	exit 2
fi

if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
	echo "Tag $TAG already exists. Delete it first:" >&2
	echo "  git tag -d $TAG && git push origin :refs/tags/$TAG" >&2
	exit 1
fi

# Annotated, not lightweight: `git describe`, which is what the page's "Current:"
# field runs, prefers annotated tags and ignores lightweight ones unless asked.
git tag -a "$TAG" -m "$TAG" "$TARGET"
echo "Created $TAG at $(git rev-parse --short "$TARGET")"

if [[ "$PUSH" == 1 ]]; then
	git push origin "refs/tags/$TAG"
	echo "Pushed $TAG to origin"
else
	echo "Not pushed (--no-push). Other machines will not see it:"
	echo "  git push origin refs/tags/$TAG"
fi

report

shown="$(displayed_version)"
if [[ "$shown" != "$TAG" ]]; then
	echo
	echo "!! $TAG was created, but $shown still sorts above it, so the page will"
	echo "!! keep showing $shown. Pick a name that version-sorts higher --"
	echo "!! compare with:  git tag --sort=v:refname | tail -5"
	exit 1
fi
