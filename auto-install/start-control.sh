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

# rebuild-acados.sh owns the cross-process lock. If an updater is already
# publishing a release this waits for it; on failure control remains stopped
# while webapp continues to serve diagnostics.
./rebuild-acados.sh --if-needed
code=$?
if [[ $code -ne 0 ]]; then
	exit "$code"
fi

exec "$REPO_ROOT/.venv/bin/python" control.py
