#!/usr/bin/env bash
#
# Rebuild the React bundle the Flask SPA blueprint serves, from whatever
# sources are currently checked out.
#
# Entry point for rebuilding OUTSIDE a version upgrade: updater.py calls this
# after every update and branch change, and /api/update/rebuild-web-ui calls it
# on demand. updater/upgrade.sh runs the same build as part of a version
# migration, so all three routes lead to one implementation --
# pifire_build_web_ui in auto-install/pifire-install-common.sh, which is never
# reimplemented here.
#
# When to run it is decided by common/web_ui_build.py, not here: this script
# always builds.
set -u

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# The common library defines fallbacks for LOG/SUDO/log when a caller has not
# set them; point LOG at the updater's own log so a rebuild triggered by an
# update is recorded beside the rest of that update.
LOG="${LOG:-$REPO/logs/update.log}"
export LOG

COMMON="$REPO/auto-install/pifire-install-common.sh"
if [ ! -r "$COMMON" ]; then
	echo " !! $COMMON is missing or unreadable -- cannot rebuild the web UI."
	exit 1
fi

# shellcheck source=../auto-install/pifire-install-common.sh
source "$COMMON"

pifire_build_web_ui "$REPO"
