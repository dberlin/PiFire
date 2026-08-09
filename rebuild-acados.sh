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

exec python3 -m tools.rebuild_acados "$@"
