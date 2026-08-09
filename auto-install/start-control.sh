#!/bin/bash

set -u

# Supervisor supplies a deliberately small environment and may omit HOME or
# PATH entirely. Native compilation still needs the system toolchain, while
# CMake and Git use HOME for harmless per-user state.
export PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
if [[ -z "${HOME:-}" ]]; then
	HOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"
	export HOME="${HOME:-/tmp}"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PIFIRE_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$REPO_ROOT" || exit 1

# Serialize against source/native/dependency/settings/cursor updates.
mkdir -p "$REPO_ROOT/controller/_native" || exit 1
TRANSACTION_LOCK="$REPO_ROOT/controller/_native/update-startup.lock"
exec 9>>"$TRANSACTION_LOCK" || exit 1
flock -x 9 || exit 1

# With the outer lock held, run the conditional gate; it owns its separate
# native publication lock.
./rebuild-acados.sh --if-needed
code=$?
if [[ $code -ne 0 ]]; then
	exit "$code"
fi
flock -u 9
exec 9>&-


exec "$REPO_ROOT/.venv/bin/python" control.py
