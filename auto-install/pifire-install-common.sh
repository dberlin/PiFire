#!/usr/bin/env bash
#
# Shared install/build steps for PiFire.
#
# Sourced by the four fresh installers and updater/rebuild-web-ui.sh. Everything
# here runs after the repository is on disk, which is why it can live in one
# file: the installers themselves are fetched standalone with `curl | bash` and
# have to be self-contained until the clone completes.
#
# Callers are expected to have set:
#   $SUDO   -- "sudo", or "" when already root
#   $LOG    -- logfile path; `log` appends to it
# and to provide a `log` function. Fallbacks are defined below so this file can
# also be sourced by hand while debugging an install.

if ! declare -F log >/dev/null 2>&1; then
	log() { echo "$@"; }
fi
: "${SUDO:=}"
: "${LOG:=/dev/null}"

# The directory this library lives in, hence the repo root.
PIFIRE_INSTALL_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIFIRE_REPO_DIR="$(cd "$PIFIRE_INSTALL_LIB_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

# pifire_require_commands cmd...
#
# Abort unless every named command is resolvable on PATH. The hint exists
# because the failure has one overwhelmingly common cause: the user-management
# tools (groupadd, usermod, useradd) live in /usr/sbin, which is on root's PATH
# but NOT on an ordinary user's on Debian and Ubuntu. The installer is meant to
# be run as that ordinary user -- it calls sudo itself, and running the whole
# script as root leaves every file it creates owned by root -- so the fix is to
# add sbin to PATH for this run, not to re-run the whole thing under sudo.
pifire_require_commands() {
	local missing=() cmd
	for cmd in "$@"; do
		command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
	done
	[[ ${#missing[@]} -eq 0 ]] && return 0

	log ""
	log " !! ====================================================================="
	log " !! Required command(s) not found on PATH: ${missing[*]}"
	log " !! ====================================================================="
	log " !! Nothing has been changed."
	log " !!"
	log " !! If these are user-management tools (groupadd/usermod/useradd), they"
	log " !! live in /usr/sbin, which is on root's PATH but not on a normal"
	log " !! user's on Debian and Ubuntu. Do NOT re-run this installer as root:"
	log " !! it invokes sudo where it needs to, and running the whole script as"
	log " !! root leaves the PiFire checkout owned by root instead of by you."
	log " !!"
	log " !! Put sbin on PATH for this run instead:"
	log " !!"
	log " !!     PATH=\"/usr/local/sbin:/usr/sbin:/sbin:\$PATH\" bash <script>"
	log " !!"
	log " !! Anything else in the list is genuinely missing and needs installing"
	log " !! with your package manager first."
	log " !! ====================================================================="
	return 1
}

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

# The React app is served from web-react/dist (blueprints/spa/routes.py), and
# dist/ is git-ignored. A fresh clone has no bundle; an update with new React
# sources keeps serving the old bundle until this runs.
# Both the installers and updater/rebuild-web-ui.sh call it for that reason.

# The bun to build with. Set by pifire_get_bun.
#
# A global, NOT a value echoed on stdout for the caller to capture: `$(...)`
# runs the function in a SUBSHELL, so anything it records belongs to that
# subshell and is gone the instant the substitution closes.
PIFIRE_BUN=""

# Where a downloaded bun is kept: inside web-react, gitignored, alongside the
# node_modules and dist it exists to produce.
#
# Persistent, not a mktemp dir. bun is ~90MB and the previous arrangement
# deleted it on exit, so every rebuild -- including one triggered from the web
# UI to recover from a failed rebuild -- paid for the whole download again,
# over whatever wifi a grill happens to have. A fresh clone re-downloading once
# is a fair price for needing no root, no packaging and no writable directory
# outside the checkout.
pifire_bun_dir() {
	echo "${1:-$PIFIRE_REPO_DIR}/web-react/.bun-toolchain"
}

# Is this a bun that will actually run? `-x` is not enough: an interrupted
# download leaves an executable-but-truncated file, and the failure then
# surfaces from inside the build as an unexplained non-zero exit.
pifire_bun_works() {
	[[ -n "$1" && -x "$1" ]] && "$1" --version >/dev/null 2>&1
}

# Sets $PIFIRE_BUN to a usable bun. Must be called WITHOUT command
# substitution -- see the comment on PIFIRE_BUN.
#
# bun is a BUILD-TIME tool only: nothing PiFire runs needs it, so it is never
# installed system-wide.
#
# Only EXPLICIT signals are honoured -- a bun on PATH, or $BUN_INSTALL, which
# is bun's own documented variable. Nothing is inferred from $HOME or from a
# version manager's private directory layout: this runs under a service
# manager, so whose home that would even be is a guess, and executing a binary
# found by guesswork is not something an install script should do.
pifire_locate_bun() {
	if command -v bun >/dev/null 2>&1; then
		PIFIRE_BUN="$(command -v bun)"
		log " + Using the bun already on PATH: $PIFIRE_BUN ($("$PIFIRE_BUN" --version 2>/dev/null))"
		return 0
	fi

	if [[ -n "${BUN_INSTALL:-}" ]] && pifire_bun_works "$BUN_INSTALL/bin/bun"; then
		PIFIRE_BUN="$BUN_INSTALL/bin/bun"
		pifire_put_bun_on_path
		log " + Using the bun at \$BUN_INSTALL: $PIFIRE_BUN ($("$PIFIRE_BUN" --version 2>/dev/null))"
		return 0
	fi

	local cached
	cached="$(pifire_bun_dir "${1:-}")/bin/bun"
	if pifire_bun_works "$cached"; then
		PIFIRE_BUN="$cached"
		pifire_put_bun_on_path
		log " + Using the downloaded bun in $(dirname "$(dirname "$cached")") ($("$PIFIRE_BUN" --version 2>/dev/null))"
		return 0
	fi

	return 1
}

# Required, not a convenience: package.json's "build" script is
# `bun run typecheck && rsbuild build`, and that inner `bun` is resolved through
# PATH by the shell bun spawns. Invoking the binary by absolute path alone gets
# "bun: command not found" from inside its own script.
pifire_put_bun_on_path() {
	export PATH="$(dirname "$PIFIRE_BUN"):$PATH"
}

# What to do when there is no toolchain and no way to get one.
pifire_report_no_bun() {
	local reason="$1"
	local toolchain="$2"
	log " !! $reason"
	log " !! No bun was found on PATH or at $toolchain/bin/bun either."
	log " !! Either give this machine network access to bun.sh, or install bun"
	log " !! yourself and point at it -- put it on the PATH the PiFire service"
	log " !! runs with, or set BUN_INSTALL=<dir> so \$BUN_INSTALL/bin/bun resolves."
	log " !! A version manager's shim will not do: this runs without the PATH"
	log " !! your login shell builds."
}

pifire_get_bun() {
	local repo="${1:-$PIFIRE_REPO_DIR}"
	pifire_locate_bun "$repo" && return 0

	# bun ships as a zip and its installer shells out to unzip. The fresh
	# installers put it in the package list; the platform-neutral updater does
	# not mutate OS packages, so an older installation may still need it.
	if ! command -v unzip >/dev/null 2>&1; then
		log " !! 'unzip' is required to unpack bun, and is not on PATH."
		log " !! Install it (apt install unzip / dnf install unzip) and re-run."
		return 1
	fi

	local toolchain
	toolchain="$(pifire_bun_dir "$repo")"
	log " + Downloading bun into $toolchain (once; kept for later rebuilds)"
	# Cleared first: locate_bun already rejected whatever is there, so it is a
	# previous download that was interrupted or is broken, and unpacking on top
	# of it would leave the two mixed.
	rm -rf "$toolchain"
	mkdir -p "$toolchain/home" || {
		log " !! Could not create $toolchain."
		return 1
	}

	# A HOME inside the toolchain, never the operator's.
	#
	# bun's installer runs with `set -u` and dereferences $HOME twice: to
	# pretty-print the destination in its success message, and to find the shell
	# rc files it appends a PATH line to. A service manager need not set HOME,
	# and this runs under one -- so the download SUCCEEDED and the installer
	# then died with "HOME: unbound variable" while tidying up, leaving us to
	# report only that bun had not landed.
	#
	# Neither use is wanted. The install location is ours via BUN_INSTALL, and
	# an rc file edited for a toolchain the operator never invokes directly is
	# pure litter -- which is what passing the real HOME through did, on every
	# machine that had one.
	if ! curl -fsSL https://bun.sh/install |
		env HOME="$toolchain/home" BUN_INSTALL="$toolchain" \
			BUN_INSTALL_CACHE_DIR="$toolchain/cache" bash >>"$LOG" 2>&1; then
		rm -rf "$toolchain"
		pifire_report_no_bun "Could not download bun from https://bun.sh/install." "$toolchain"
		return 1
	fi
	if ! pifire_bun_works "$toolchain/bin/bun"; then
		rm -rf "$toolchain"
		pifire_report_no_bun "The bun installer ran but left nothing runnable behind." "$toolchain"
		return 1
	fi
	PIFIRE_BUN="$toolchain/bin/bun"
	pifire_put_bun_on_path
	log " + bun ready ($("$PIFIRE_BUN" --version 2>/dev/null))"
}

# pifire_build_web_ui [repo_dir]
#
# Returns non-zero on failure so a caller can decide whether that is fatal.
pifire_build_web_ui() {
	local repo="${1:-$PIFIRE_REPO_DIR}"
	local web="$repo/web-react"

	if [[ ! -f "$web/package.json" ]]; then
		log " !! No $web/package.json -- cannot build the web UI."
		return 1
	fi

	# Not `$(pifire_get_bun)` -- that subshell would take everything it set
	# with it. See PIFIRE_BUN.
	pifire_get_bun "$repo" || return 1

	log " + Installing web UI dependencies"
	# `set -o pipefail` inside each subshell is load-bearing: without it the
	# pipeline's status is TEE's, so a failed install or build reports success
	# and the only thing that notices is the artifact check below.
	#
	# --frozen-lockfile: bun.lock is committed, and a build that silently
	# resolved different versions than were tested is not the tested build.
	if ! (
		set -o pipefail
		cd "$web" && "$PIFIRE_BUN" install --frozen-lockfile 2>&1 | tee -a "$LOG"
	); then
		log " !! Web UI dependency install failed."
		return 1
	fi

	log " + Building the web UI"
	if ! (
		set -o pipefail
		cd "$web" && "$PIFIRE_BUN" run build 2>&1 | tee -a "$LOG"
	); then
		log " !! Web UI build failed."
		return 1
	fi

	# `bun run build` runs typecheck first and would have failed loudly, but
	# the artifact is what the Flask SPA blueprint actually serves, so check
	# for it rather than trusting the exit status.
	if [[ ! -f "$web/dist/index.html" ]]; then
		log " !! Build reported success but $web/dist/index.html is missing."
		return 1
	fi
	log " + Web UI built: $web/dist"
}

# pifire_install_acados_prerequisites debian|fedora
pifire_install_acados_prerequisites() {
	local platform="$1"
	local command=()
	case "$platform" in
	debian) command=(apt-get install -y build-essential cmake) ;;
	fedora) command=(dnf -y install gcc gcc-c++ make cmake) ;;
	*)
		log " !! Unsupported acados prerequisite platform: $platform"
		return 2
		;;
	esac
	log " + Installing acados C/C++ and CMake prerequisites"
	if ! (
		set -o pipefail
		$SUDO "${command[@]}" 2>&1 | tee -a "$LOG"
	); then
		log " !! Failed to install acados native prerequisites."
		return 1
	fi
}

# pifire_rebuild_acados [repo_dir]
#
# Run after Python synchronization (the installer ordering contract) and before
# Supervisor can start a native consumer. The public command owns build
# serialization and atomic runtime publication.
pifire_rebuild_acados() {
	local repo="${1:-$PIFIRE_REPO_DIR}"
	log " + Checking the acados native runtime"
	if (
		set -o pipefail
		cd "$repo" && ./rebuild-acados.sh --if-needed 2>&1 | tee -a "$LOG"
	); then
		log " + acados native runtime ready"
		return 0
	else
		local code=$?
		log " !! The acados native runtime could not be built. Installation cannot continue."
		return "$code"
	fi
}

# Synchronize Python and publish native runtime as one fail-fast installer gate.
pifire_sync_python_and_rebuild_acados() {
	local repo="${1:-$PIFIRE_REPO_DIR}"
	log " + Installing module dependencies from pyproject.toml"
	if ! (
		set -o pipefail
		cd "$repo" && uv sync --no-dev --inexact 2>&1 | tee -a "$LOG"
	); then
		log " !! Python dependency install failed. Installation cannot continue."
		return 1
	fi
	log " + Python dependency installation complete."
	pifire_rebuild_acados "$repo"
}

# ---------------------------------------------------------------------------
# Device access
# ---------------------------------------------------------------------------
#
# The target setup is sway (Wayland) running the QtQuick display, on a panel
# that is a USB HID touchscreen, with I2C peripherals -- so the control and
# display processes need DRM/render nodes, libinput devices, a seat, hidraw,
# and /dev/i2c-*, all as an ordinary user.

# Groups created when absent, because the thing that needs them may not exist
# yet either. `i2c` in particular ships on Raspberry Pi OS and generally does
# not on x86 distros, while the udev rules below hand /dev/i2c-* to it.
PIFIRE_CREATE_GROUPS=(pifire i2c)

# Groups joined only when the distro already defines them. Creating these would
# produce empty groups owning nothing -- `gpio`/`spi` are Raspberry Pi OS
# conventions, and `seat` belongs to whichever seat manager is installed.
PIFIRE_OPTIONAL_GROUPS=(
	dialout # USB serial: Numato relay board, SEN0628, USB probe adapters
	plugdev # raw USB / HID bridges: CP2112, MCP2221, FT232H
	video   # DRM/KMS: /dev/dri/card* -- sway's output
	render  # GPU render nodes: /dev/dri/renderD* -- Qt's GPU rendering
	input   # libinput: the touchscreen and any buttons
	seat    # seatd, which sway takes its DRM/input handles from
	tty     # VT access for a compositor started outside a login session
	gpio    # /dev/gpiochip* (Raspberry Pi OS)
	spi     # /dev/spidev*   (Raspberry Pi OS)
)

# The highest GID that still counts as a system group. login.defs is
# authoritative; 999 is the Debian/Fedora default when it does not say.
pifire_sys_gid_max() {
	local v
	v=$(awk '/^[[:space:]]*SYS_GID_MAX[[:space:]]/ {print $2}' /etc/login.defs 2>/dev/null | tail -1)
	[[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo 999
}

# First unused GID in the system range, or empty if the range is full.
pifire_free_system_gid() {
	local min max gid
	min=$(awk '/^[[:space:]]*SYS_GID_MIN[[:space:]]/ {print $2}' /etc/login.defs 2>/dev/null | tail -1)
	[[ "$min" =~ ^[0-9]+$ ]] || min=100
	max=$(pifire_sys_gid_max)
	# Downwards from the top of the range: the low end is where the distro's
	# own long-established groups live, so starting there maximises collisions
	# with GIDs a future package might expect to claim.
	for ((gid = max; gid >= min; gid--)); do
		getent group "$gid" >/dev/null 2>&1 || {
			echo "$gid"
			return 0
		}
	done
}

# pifire_ensure_system_group NAME
#
# Create NAME as a system group, or convert an existing non-system one.
#
# The conversion exists because earlier PiFire installers ran a plain
# `groupadd pifire`, which allocates from the LOGIN range -- and udev warns
# that "device node ownership by non-system accounts is deprecated and will be
# removed in the future", while auto-install/udev/99-pifire.rules hands this
# group device nodes. An install that predates those rules would otherwise keep
# a group that stops working at some future systemd release.
#
# Changing a GID renumbers the group, and file ownership is stored NUMERICALLY
# -- so every file still carrying the old GID becomes owned by a group that no
# longer exists. Files under the PiFire install are re-grouped here; anything
# outside it is reported rather than guessed at, because a filesystem-wide
# chgrp is not something an installer should do unasked.
pifire_ensure_system_group() {
	local grp="$1" old_gid new_gid sys_max repo

	if ! getent group "$grp" >/dev/null 2>&1; then
		log "   - creating system group $grp"
		$SUDO groupadd -f -r "$grp" 2>/dev/null || true
		return 0
	fi

	old_gid=$(getent group "$grp" | cut -d: -f3)
	sys_max=$(pifire_sys_gid_max)
	if [[ ! "$old_gid" =~ ^[0-9]+$ ]] || ((old_gid <= sys_max)); then
		return 0 # already a system group -- nothing to do
	fi

	new_gid=$(pifire_free_system_gid)
	if [[ -z "$new_gid" ]]; then
		log " ! Group '$grp' has GID $old_gid (not a system group) and the system"
		log " ! GID range is full, so it cannot be converted. udev may refuse to"
		log " ! apply device ownership to it in a future release."
		return 0
	fi

	log "   - converting '$grp' from GID $old_gid to system GID $new_gid"
	if ! $SUDO groupmod -g "$new_gid" "$grp" 2>&1 | tee -a "$LOG"; then
		log " ! Could not renumber '$grp'; leaving it at GID $old_gid."
		return 0
	fi

	# Re-group what the old GID owned inside the install. The installers chown
	# the tree right after this anyway; the updater does not, which is exactly
	# the path where a stale numeric group would otherwise survive.
	repo="${PIFIRE_REPO_DIR:-/usr/local/bin/pifire}"
	[[ -d "$repo" ]] && $SUDO find "$repo" -gid "$old_gid" -exec chgrp "$grp" {} + 2>/dev/null

	# Anything else on the box that carried the old GID is now orphaned. Say
	# so with the command to find it rather than sweeping the filesystem.
	log " ! '$grp' was renumbered $old_gid -> $new_gid. Files OUTSIDE $repo that"
	log " ! belonged to it now show a numeric group. Find them with:"
	log " !     sudo find / -xdev -gid $old_gid"
}

# pifire_add_hardware_groups user...
pifire_add_hardware_groups() {
	local user grp
	for grp in "${PIFIRE_CREATE_GROUPS[@]}"; do
		pifire_ensure_system_group "$grp"
	done

	for user in "$@"; do
		[[ -n "$user" ]] || continue
		id "$user" >/dev/null 2>&1 || continue
		for grp in "${PIFIRE_CREATE_GROUPS[@]}" "${PIFIRE_OPTIONAL_GROUPS[@]}"; do
			getent group "$grp" >/dev/null 2>&1 || continue
			if $SUDO usermod -a -G "$grp" "$user" 2>/dev/null; then
				log "   - $user -> $grp"
			fi
		done
	done
	log " ! Group changes only apply to NEW logins -- the reboot at the end of"
	log " ! this install is what picks them up."
}

# pifire_install_udev_rules [repo_dir]
#
# Hands the USB serial / HID / I2C / backlight nodes to the pifire group, and
# creates the stable /dev/pifire-numato symlink for the relay board.
pifire_install_udev_rules() {
	local repo="${1:-$PIFIRE_REPO_DIR}"
	local src="$repo/auto-install/udev/99-pifire.rules"

	if [[ ! -f "$src" ]]; then
		log " ! $src not found; skipping udev rules."
		return 0
	fi
	log " + Installing udev rules to /etc/udev/rules.d/99-pifire.rules"
	$SUDO install -m 0644 "$src" /etc/udev/rules.d/99-pifire.rules 2>&1 | tee -a "$LOG"

	# i2c-dev is what creates /dev/i2c-*; without it the bus nodes the rules
	# above govern never appear, and an I2C peripheral reads as "not detected".
	log " + Ensuring the i2c-dev module loads at boot"
	echo "i2c-dev" | $SUDO tee /etc/modules-load.d/pifire-i2c.conf >/dev/null
	$SUDO modprobe i2c-dev 2>/dev/null || log " ! modprobe i2c-dev failed (no I2C on this machine?)"

	# Reload so the rules apply to devices already plugged in, rather than
	# only to whatever is connected after the next reboot.
	$SUDO udevadm control --reload-rules 2>&1 | tee -a "$LOG" || log " ! udevadm reload failed (continuing)."
	$SUDO udevadm trigger 2>&1 | tee -a "$LOG" || log " ! udevadm trigger failed (continuing)."
}

# pifire_prepare_log_dir <repo> <user>
#
# The three supervisor programs do not all run as the same user: control and
# webapp run as the install user, display runs as root, because it needs the
# seat sway and seatd hand out. They write the SAME files -- control.py and
# display_process.py both open logs/control.log and logs/events.log -- so
# whichever starts first decides who owns them, and on a fresh install that is
# display, as root. control then cannot append to its own log and dies.
#
# Setgid on the directory, plus umask=002 on the programs (see
# auto-install/supervisor/*.conf), makes the start order stop mattering: every
# log lands group pifire and group-writable whoever creates it, including the
# files RotatingFileHandler opens on rollover.
#
# Call this AFTER any recursive chmod of the install tree, which would strip
# the setgid bit back off.
pifire_prepare_log_dir() {
	local logs="$1/logs" user="$2"

	log " + Making $logs writable by both root and $user"
	$SUDO mkdir -p "$logs" || return 1
	$SUDO chown "$user":pifire "$logs" || return 1
	$SUDO chmod 2775 "$logs" || return 1
	# Repair logs an earlier install left owned by root alone. Ownership is
	# left as it is -- group access is what the other process needs.
	$SUDO find "$logs" -maxdepth 1 -type f -exec chgrp pifire {} + -exec chmod g+w {} + 2>/dev/null
	return 0
}


# pifire_prepare_datastore_dir <repo> <user> [group]
#
# updater.py initializes SQLite before supervisor starts. The installer shell
# therefore creates pifire.db under the install user's primary group unless the
# repository directory inherits pifire. SQLite creates a new main database as
# 0644 and copies that mode to later WAL/SHM sidecars, so umask alone cannot add
# group-write. Setgid supplies the shared group; repairing the main database to
# 0664 after initialization makes every later sidecar writable by both root and
# the install user. Existing database artifacts are repaired at the same time.
#
# Call this AFTER recursive chmod, BEFORE the first datastore initialization,
# and AGAIN after that initialization has completed successfully.
pifire_prepare_datastore_dir() {
	local repo="$1" user="$2" group="${3:-pifire}"

	log " + Making the datastore writable by root and $user"
	$SUDO chown "$user:$group" "$repo" || return 1
	$SUDO chmod 2775 "$repo" || return 1
	$SUDO find "$repo" -maxdepth 1 -type f \
		\( -name "pifire.db" -o -name "pifire.db-wal" -o -name "pifire.db-shm" -o -name "pifire.db-journal" \) \
		-exec chgrp "$group" {} + -exec chmod 0664 {} + 2>/dev/null || return 1
	umask 002
	return 0
}
