#!/bin/bash

set -u

export PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PIFIRE_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
OS_RELEASE="${PIFIRE_OS_RELEASE:-/etc/os-release}"
SUPERVISOR_ROOT="${PIFIRE_SUPERVISOR_ROOT:-/etc}"
DB_PATH="${PIFIRE_DB_PATH:-$REPO_ROOT/pifire.db}"
TRANSACTION_FILE="$REPO_ROOT/controller/_native/update-transaction.json"
PREREQUISITES_ONLY=false
if [[ "${1:-}" == "--prerequisites-only" ]]; then
	PREREQUISITES_ONLY=true
elif [[ $# -ne 0 ]]; then
	echo "usage: $0 [--prerequisites-only]" >&2
	exit 2
fi

run_root() {
	if [[ $EUID -eq 0 ]]; then
		"$@"
	else
		sudo "$@"
	fi
}

install_prerequisites() {
	if [[ ! -r "$OS_RELEASE" ]]; then
		echo "Cannot identify this system: $OS_RELEASE is unreadable" >&2
		return 1
	fi
	# shellcheck disable=SC1090
	. "$OS_RELEASE"
	local family=" ${ID:-} ${ID_LIKE:-} "
	local missing=false
	if [[ "${PIFIRE_FORCE_PREREQUISITES:-0}" == "1" ]]; then
		missing=true
	elif ! command -v cmake >/dev/null 2>&1; then
		missing=true
	elif ! command -v cc >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1; then
		missing=true
	elif ! command -v c++ >/dev/null 2>&1 && ! command -v g++ >/dev/null 2>&1; then
		missing=true
	fi
	if [[ "$missing" == false ]]; then
		echo "acados C/C++ and CMake prerequisites are already available"
		return 0
	fi

	case "$family" in
	*" fedora "* | *" rhel "* | *" centos "*)
		run_root dnf -y install gcc gcc-c++ make cmake
		;;
	*" debian "* | *" ubuntu "*)
		run_root apt-get install -y build-essential cmake
		;;
	*)
		echo "Unsupported package platform: ID=${ID:-} ID_LIKE=${ID_LIKE:-}" >&2
		return 1
		;;
	esac
}

write_terminal_failure() {
	local reason="$1"
	python3 - "$DB_PATH" "$reason" <<'PY'
import json
import sqlite3
import sys

path, reason = sys.argv[1:]
try:
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for key, value in (
            ("updater:percent", -1),
            ("updater:status", "Acados native migration failed"),
            ("updater:output", reason),
        ):
            connection.execute(
                "INSERT INTO kv(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
except Exception as exc:
    print(f"Could not persist updater failure status: {exc}", file=sys.stderr)
PY
}

terminate_old_updater() {
	local parent="${PIFIRE_UPDATER_PID:-$PPID}"
	if [[ "$parent" =~ ^[0-9]+$ ]] && ((parent > 1)); then
		kill -TERM "$parent" 2>/dev/null || true
	fi
}

previous_revision=""
previous_branch=""
runtime_target="__PIFIRE_MISSING__"
current_link="$REPO_ROOT/controller/_native/current"

record_snapshot() {
	local current_branch reflog
	cd "$REPO_ROOT" || return 1
	previous_revision="$(git rev-parse 'HEAD@{1}' 2>/dev/null)" || return 1
	git rev-parse --verify "${previous_revision}^{commit}" >/dev/null 2>&1 || return 1
	current_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || return 1
	reflog="$(git reflog -1 --format=%gs 2>/dev/null)" || return 1
	case "$reflog" in
	"checkout: moving from "*" to "*)
		previous_branch="${reflog#checkout: moving from }"
		previous_branch="${previous_branch%% to *}"
		;;
	*) previous_branch="$current_branch" ;;
	esac
	[[ -n "$previous_branch" ]] || return 1
	if [[ -L "$current_link" ]]; then
		runtime_target="$(readlink "$current_link")" || return 1
	elif [[ -e "$current_link" ]]; then
		echo "$current_link is not a symbolic link" >&2
		return 1
	fi

	mkdir -p "$(dirname "$TRANSACTION_FILE")" || return 1
	PREVIOUS_REVISION="$previous_revision" PREVIOUS_BRANCH="$previous_branch" \
		RUNTIME_TARGET="$runtime_target" python3 - "$TRANSACTION_FILE" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = {
    "schema": 1,
    "previous_revision": os.environ["PREVIOUS_REVISION"],
    "previous_branch": os.environ["PREVIOUS_BRANCH"],
    "runtime_target": os.environ["RUNTIME_TARGET"],
}
if not payload["previous_revision"] or not payload["previous_branch"]:
    raise SystemExit("incomplete updater transaction snapshot")
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
os.replace(temporary, path)
PY
}

restore_snapshot() {
	local result=0 tmp_link
	cd "$REPO_ROOT" || return 1
	git checkout -f "$previous_branch" || result=1
	git reset --hard "$previous_revision" || result=1
	if [[ "$runtime_target" == "__PIFIRE_MISSING__" ]]; then
		rm -f "$current_link" || result=1
	else
		tmp_link="${current_link}.rollback.$$"
		rm -f "$tmp_link"
		ln -s "$runtime_target" "$tmp_link" && mv -Tf "$tmp_link" "$current_link" || result=1
	fi
	return "$result"
}

fail_bootstrap() {
	local reason="$1"
	if [[ -n "$previous_revision" && -n "$previous_branch" ]]; then
		if ! restore_snapshot; then
			reason="$reason; source/runtime rollback also reported an error"
		fi
	fi
	write_terminal_failure "$reason"
	echo "$reason" >&2
	terminate_old_updater
	exit 1
}

if [[ "$PREREQUISITES_ONLY" == true ]]; then
	install_prerequisites
	exit $?
fi

# This default path is the historical-updater bootstrap. It is deliberately
# shell + Python standard library only: the live process imported the old tree,
# and no Python environment may be synchronized before this native gate.
record_snapshot || fail_bootstrap "Could not validate the previous source revision, branch, and runtime pointer"
install_prerequisites || fail_bootstrap "Could not install acados C/C++ and CMake prerequisites"
cd "$REPO_ROOT" || fail_bootstrap "Could not enter the updated PiFire checkout"
./rebuild-acados.sh --if-needed || fail_bootstrap "Acados native rebuild failed"

# Deploy only after native success. The existing run-as user is part of the
# installed machine's configuration and must survive a source-tree refresh.
. "$OS_RELEASE"
family=" ${ID:-} ${ID_LIKE:-} "
case "$family" in
*" fedora "* | *" rhel "* | *" centos "*)
	target="$SUPERVISOR_ROOT/supervisord.d/control.ini"
	;;
*" debian "* | *" ubuntu "*)
	target="$SUPERVISOR_ROOT/supervisor/conf.d/control.conf"
	;;
*) fail_bootstrap "Unsupported Supervisor platform: ID=${ID:-} ID_LIKE=${ID_LIKE:-}" ;;
esac
install_user=""
if [[ -r "$target" ]]; then
	install_user="$(sed -n 's/^user=//p' "$target" | tail -n 1)"
fi
if [[ -z "$install_user" ]]; then
	install_user="${PIFIRE_INSTALL_USER:-${SUDO_USER:-${USER:-}}}"
fi
[[ -n "$install_user" && "$install_user" != root ]] || fail_bootstrap "Could not preserve the configured PiFire install user"
control_tmp="$(mktemp)" || fail_bootstrap "Could not stage the Supervisor control definition"
cat "$REPO_ROOT/auto-install/supervisor/control.conf" >"$control_tmp" || fail_bootstrap "Could not read control.conf"
printf 'user=%s\n' "$install_user" >>"$control_tmp"
if ! run_root bash -c 'set -eu; src=$1; dst=$2; tmp="${dst}.new.$$"; cat "$src" >"$tmp"; chmod 0644 "$tmp"; mv -f "$tmp" "$dst"' _ "$control_tmp" "$target"; then
	rm -f "$control_tmp"
	fail_bootstrap "Could not deploy the Supervisor control definition"
fi
rm -f "$control_tmp" "$TRANSACTION_FILE"
exit 0
