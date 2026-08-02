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
#   updater/tag-release.sh v1.11-dannyb --git        # force the git path in a jj checkout
#   updater/tag-release.sh v1.11-dannyb --bookmark b # jj: which bookmark to move and push
#
# This repo is a jj workspace colocated with git, so the script drives jj when
# it finds one and git otherwise; --jj / --git force the choice. The difference
# matters: `git commit` in a colocated repo silently works, and leaves jj to
# reconcile a commit that appeared underneath it. jj mode does the same job with
# `jj describe` + `jj new` + `jj bookmark set` + `jj git push` instead.
#
# TAGS ARE ALWAYS GIT, in both modes. jj has no tag support at all, and the two
# things that decide the updater page's "Remote:" line -- `git describe` and
# `git tag --sort=v:refname --merged` -- are git-side by nature. jj mode only
# changes how the COMMIT is made and pushed.
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

MANIFEST="updater/updater_manifest.json"
TAG=""
VERSION=""
BUILD=""
BOOKMARK=""
PUSH=1
COMMIT=1
CHECK_ONLY=0
VCS=""

while [[ $# -gt 0 ]]; do
	case "$1" in
	--check) CHECK_ONLY=1 ;;
	--no-push) PUSH=0 ;;
	--tag-only) COMMIT=0 ;;
	--jj) VCS="jj" ;;
	--git) VCS="git" ;;
	--bookmark)
		BOOKMARK="${2:-}"
		shift
		;;
	--version)
		VERSION="${2:-}"
		shift
		;;
	--build)
		BUILD="${2:-}"
		shift
		;;
	-h | --help)
		sed -n '2,45p' "$0" | sed 's/^# \?//'
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

# Chosen before the first cd so a jj workspace that is not colocated still finds
# its own root rather than a parent repo's.
if [[ -z "$VCS" ]]; then
	if command -v jj >/dev/null 2>&1 && jj workspace root >/dev/null 2>&1; then
		VCS="jj"
	else
		VCS="git"
	fi
fi

if [[ "$VCS" == "jj" ]]; then
	cd "$(jj workspace root)"
else
	cd "$(git rev-parse --show-toplevel)"
fi

json() { python3 -c "import json,sys; print(json.load(open('$MANIFEST'))$1)"; }

# ---------------------------------------------------------------------------
# VCS seam. Everything below the line talks to these, never to jj/git directly,
# except where the comment says the operation is git-only by nature.
# ---------------------------------------------------------------------------

# The commit the tag will go on, and that `git describe` will be asked about.
# jj's @ is the working copy, so the release commit is its parent once `jj new`
# has sealed it; naming the commit explicitly beats relying on HEAD having been
# exported yet.
release_commit() {
	if [[ "$VCS" == "jj" ]]; then
		jj --no-pager log -r @- --no-graph -T 'commit_id'
	else
		git rev-parse HEAD
	fi
}

# The bookmark/branch the release lands on. In jj this is NOT implied by the
# working copy -- bookmarks do not follow new commits the way git branches do --
# so it is the nearest one behind @, and --bookmark overrides.
resolve_bookmark() {
	[[ -z "$BOOKMARK" ]] || return 0
	if [[ "$VCS" == "jj" ]]; then
		local found
		found="$(jj --no-pager log -r 'heads(::@ & bookmarks())' --no-graph -T 'bookmarks ++ "\n"' | tr ' ' '\n' | sed '/^$/d')"
		if [[ "$(wc -l <<<"$found")" -ne 1 ]]; then
			echo "Refusing: cannot tell which bookmark to move (found: ${found//$'\n'/, })." >&2
			echo "Pass --bookmark <name>." >&2
			exit 1
		fi
		BOOKMARK="$found"
	else
		BOOKMARK="$(git symbolic-ref --quiet --short HEAD || true)"
	fi
}

# The ref the updater measures "Remote:" against: origin/<branch>, or HEAD when
# there is none -- the same choice updater.py's get_remote_version makes.
measured_against() {
	local name="$BOOKMARK"
	if [[ "$VCS" == "git" && -z "$name" ]]; then
		name="$(git symbolic-ref --quiet --short HEAD || true)"
	fi
	if [[ -n "$name" ]] && git rev-parse --verify --quiet "origin/$name" >/dev/null; then
		echo "origin/$name"
	else
		echo "HEAD"
	fi
}

# The manifest as the release commit records it -- read from the commit, never
# from the working copy, so --tag-only cannot tag a commit that says something
# other than what was asked for.
committed_manifest_version() {
	if [[ "$VCS" == "jj" ]]; then
		jj --no-pager file show -r @- "$MANIFEST"
	else
		git show "HEAD:$MANIFEST"
	fi | python3 -c 'import json,sys; print(json.load(sys.stdin)["metadata"]["versions"]["server"])'
}

commit_manifest() {
	if [[ "$VCS" == "jj" ]]; then
		# Only the manifest, matching the git path: this repo often has other
		# work in progress. jj has no path-scoped commit, so instead of
		# splitting the change out afterwards the working copy is required to
		# be clean going in -- which also stops a release commit quietly
		# carrying someone's half-finished edit.
		local dirty
		dirty="$(jj --no-pager diff --name-only)"
		if [[ -n "$dirty" ]]; then
			echo "Refusing: the jj working copy has changes, and a release commit must carry" >&2
			echo "only the manifest. Describe or abandon them first, then re-run:" >&2
			sed 's/^/  /' <<<"$dirty" >&2
			exit 1
		fi
	fi

	write_manifest

	if [[ "$VCS" == "jj" ]]; then
		if [[ -z "$(jj --no-pager diff --name-only)" ]]; then
			echo "($MANIFEST was already at those values; nothing to commit)"
			return 0
		fi
		jj describe -m "chore(release): $TAG (server $VERSION, build $BUILD)" >/dev/null
		# Seals it: @ becomes a fresh empty commit, and the release is @-, which
		# is what release_commit and committed_manifest_version read. Without
		# this the release would still be the working copy, and any later edit
		# would silently amend a commit that is about to be tagged and pushed.
		jj new >/dev/null
		jj bookmark set "$BOOKMARK" -r @- >/dev/null
		echo "Committed $(release_commit | cut -c1-12) and moved $BOOKMARK to it"
	else
		if git diff --quiet -- "$MANIFEST"; then
			echo "($MANIFEST was already at those values; nothing to commit)"
		else
			# Only the manifest: this repo often has other work in progress.
			git add -- "$MANIFEST"
			git commit -q -m "chore(release): $TAG (server $VERSION, build $BUILD)" -- "$MANIFEST"
			echo "Committed $(git rev-parse --short HEAD)"
		fi
	fi
}

push_commit() {
	if [[ "$VCS" == "jj" ]]; then
		jj git push -b "$BOOKMARK"
	elif [[ -n "$BOOKMARK" ]]; then
		git push origin "$BOOKMARK"
	else
		echo "Detached HEAD: not pushing the commit, only the tag."
	fi
}

write_manifest() {
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
}

report() {
	echo
	echo "VCS mode         : $VCS${BOOKMARK:+ (bookmark $BOOKMARK)}"
	echo "Measured against : $(measured_against)"
	echo "Current: will show v$(json "['metadata']['versions']['server']") ($(git describe --tags --always "$(release_commit)"))"
	echo "Remote:  will show $(git tag --sort=v:refname --merged "$(measured_against)" | tail -n 1)"
}

# ---------------------------------------------------------------------------

resolve_bookmark

if [[ "$CHECK_ONLY" == 1 ]]; then
	report
	exit 0
fi

if [[ -z "$TAG" ]]; then
	echo "usage: $0 <tag> [--version X.Y.Z] [--build N] [--tag-only] [--no-push]" >&2
	echo "       [--jj|--git] [--bookmark NAME]   (or --check)" >&2
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
	commit_manifest
fi

# Checked rather than assumed, so --tag-only cannot quietly tag a commit whose
# manifest says something else.
HEAD_VERSION="$(committed_manifest_version)"
if [[ "$HEAD_VERSION" != "$VERSION" ]]; then
	echo "Refusing: the release commit's manifest says server $HEAD_VERSION, not $VERSION." >&2
	echo "Commit the manifest bump before tagging, or drop --tag-only." >&2
	exit 1
fi

# Git, in both modes: jj has no tags. Annotated, not lightweight -- `git
# describe`, which is what the page's "Current:" field runs, prefers annotated
# tags and ignores lightweight ones unless asked. Named explicitly rather than
# tagging HEAD, so jj mode tags the commit it just sealed even if HEAD has not
# been exported.
COMMIT_ID="$(release_commit)"
git tag -a "$TAG" -m "$TAG" "$COMMIT_ID"
echo "Tagged $(git rev-parse --short "$COMMIT_ID") as $TAG"

if [[ "$PUSH" == 1 ]]; then
	push_commit
	git push origin "refs/tags/$TAG"
	echo "Pushed $TAG to origin"
else
	echo "Not pushed (--no-push):"
	if [[ "$VCS" == "jj" ]]; then
		echo "  jj git push -b $BOOKMARK && git push origin refs/tags/$TAG"
	else
		echo "  git push origin HEAD refs/tags/$TAG"
	fi
fi

report

problems=0
described="$(git describe --tags --always "$COMMIT_ID")"
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
