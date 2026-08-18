#!/usr/bin/env bash
set -euo pipefail

repository_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$repository_root"

case "${1-}" in
  "") ;;
  --if-needed)
    if (( $# != 1 )); then
      printf 'usage: %s [--if-needed]\n' "$0" >&2
      exit 2
    fi
    ;;
  *)
    printf 'usage: %s [--if-needed]\n' "$0" >&2
    exit 2
    ;;
esac

# tools/rebuild_acados is repository code and needs the interpreter the
# repository targets, not whatever `python3` the host happens to expose. The
# installer synchronizes the venv before it calls this script (the ordering
# contract in auto-install/pifire-install-common.sh), so the absolute path is
# present on the install path; the fallback keeps a bare checkout working.
interpreter="$repository_root/.venv/bin/python3"
if [[ ! -x "$interpreter" ]]; then
  interpreter=python3
fi

exec "$interpreter" -m tools.rebuild_acados "$@"
