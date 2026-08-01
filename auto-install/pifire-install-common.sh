#!/usr/bin/env bash
#
# Shared install/upgrade steps for PiFire.
#
# Sourced by auto-install/install.sh, install-debian.sh, install-fedora.sh,
# pifire-dietpi.sh and updater/upgrade.sh. Everything here runs AFTER the repo
# is on disk, which is why it can live in one file: the installers themselves
# are fetched standalone with `curl | bash` and have to be self-contained until
# the clone completes.
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
# dist/ is git-ignored -- it is a build artifact, not a checked-in one. So a
# fresh clone has no web interface at all until this runs, and an upgrade that
# pulls new React sources keeps serving the OLD bundle until it runs again.
# Both the installers and updater/upgrade.sh call it for that reason.

# Where a throwaway bun gets unpacked, when one has to be fetched.
PIFIRE_BUN_TMPDIR=""

# The bun to build with. Set by pifire_get_bun.
#
# A global, NOT a value echoed on stdout for the caller to capture: `$(...)`
# runs the function in a SUBSHELL, so the temp directory it recorded and the
# EXIT trap it registered both belong to that subshell -- which means the trap
# fires the instant the substitution closes and deletes the freshly downloaded
# bun before the build can use it. That failure only appears on machines
# WITHOUT bun already installed, i.e. every real install.
PIFIRE_BUN=""

# Remove the throwaway toolchain. Registered as an EXIT trap by the fetch path
# so an interrupted or failed build does not leave ~90MB behind.
pifire_cleanup_bun() {
	if [[ -n "$PIFIRE_BUN_TMPDIR" && -d "$PIFIRE_BUN_TMPDIR" ]]; then
		rm -rf "$PIFIRE_BUN_TMPDIR"
		PIFIRE_BUN_TMPDIR=""
	fi
}

# Sets $PIFIRE_BUN to a usable bun. Must be called WITHOUT command
# substitution -- see the comment on PIFIRE_BUN.
#
# bun is a BUILD-TIME tool only: nothing PiFire runs needs it, so it is never
# installed system-wide. An existing bun on PATH is used as-is (and never
# removed); otherwise one is unpacked into a temp directory that
# pifire_cleanup_bun deletes.
# Where bun ends up when something other than PATH put it there.
#
# A rebuild triggered from the web UI runs in a process descended from the
# service manager, whose PATH is the system default -- not the one an
# interactive shell builds from ~/.profile, ~/.bashrc or a version manager's
# activation hook. So a machine with a perfectly good bun a shell can see took
# the download path anyway, and then failed on a network the shell also had.
#
# Real binaries, not shims: a mise or asdf shim re-execs its manager, which is
# itself only on the interactive PATH.
pifire_bun_candidates() {
	local home="${HOME:-}"
	# `:+` rather than `:-`: an unset BUN_INSTALL leaves an empty line the
	# callers skip, instead of duplicating the ~/.bun default below it.
	printf '%s\n' \
		"${BUN_INSTALL:+$BUN_INSTALL/bin/bun}" \
		"$home/.bun/bin/bun" \
		"${MISE_DATA_DIR:-$home/.local/share/mise}/installs/bun/latest/bin/bun" \
		"$home/.asdf/installs/bun/latest/bin/bun" \
		/usr/local/bin/bun \
		/opt/bun/bin/bun \
		/usr/bin/bun
}

# Why the build has no toolchain, and what to do about it.
#
# The download is a fallback, so its failure is only half the story: the other
# half is that nothing was found locally, and an operator who can run `bun`
# themselves needs to know WHERE this looked before concluding the machine has
# no bun. Both halves, and the two ways out, in one place.
pifire_report_no_bun() {
	local reason="$1"
	log " !! $reason"
	log " !! No existing bun was found either. Looked on PATH and at:"
	local candidate
	while read -r candidate; do
		[[ -n "$candidate" ]] && log " !!   $candidate"
	done < <(pifire_bun_candidates)
	log " !! Either give this machine network access to bun.sh, or install bun"
	log " !! somewhere above -- \`curl -fsSL https://bun.sh/install | bash\` puts"
	log " !! it in ~/.bun/bin. A version manager's shim will not do: this runs"
	log " !! without the PATH your login shell builds."
}

# Set $PIFIRE_BUN from a bun already on this machine, or return 1. Touches no
# network -- kept separate from pifire_get_bun so it can be exercised without
# one, and so the download stays visibly a fallback rather than a step.
pifire_locate_bun() {
	if command -v bun >/dev/null 2>&1; then
		PIFIRE_BUN="$(command -v bun)"
		log " + Using the bun already on PATH: $PIFIRE_BUN ($("$PIFIRE_BUN" --version 2>/dev/null))"
		return 0
	fi

	local candidate
	while read -r candidate; do
		[[ -n "$candidate" && -x "$candidate" ]] || continue
		PIFIRE_BUN="$candidate"
		# Its own directory goes on PATH for the same reason the downloaded
		# one's does: package.json's "build" script re-invokes `bun`, resolved
		# through PATH by the shell bun spawns.
		export PATH="$(dirname "$candidate"):$PATH"
		log " + Using the bun installed at $PIFIRE_BUN ($("$PIFIRE_BUN" --version 2>/dev/null))"
		return 0
	done < <(pifire_bun_candidates)

	return 1
}

pifire_get_bun() {
	pifire_locate_bun && return 0

	# bun ships as a zip and its installer shells out to unzip. The installers
	# put it in the package list, but an install predating that -- upgrading
	# through updater/upgrade.sh, which installs no packages -- will not have
	# it, and the failure surfaces only as an unzip error buried in the log.
	if ! command -v unzip >/dev/null 2>&1; then
		log " !! 'unzip' is required to unpack bun, and is not on PATH."
		log " !! Install it (apt install unzip / dnf install unzip) and re-run."
		return 1
	fi

	log " + Fetching a temporary bun to build the web UI (not installed system-wide)"
	PIFIRE_BUN_TMPDIR="$(mktemp -d -t pifire-bun-XXXXXX)" || {
		log " !! Could not create a temp directory for bun."
		return 1
	}
	# Cleans up if the script dies before pifire_build_web_ui finishes.
	trap pifire_cleanup_bun EXIT

	# BUN_INSTALL puts it at $BUN_INSTALL/bin/bun. No sudo: this is a
	# throwaway under the invoking user's temp dir, which is also what lets
	# the build run without root.
	if ! curl -fsSL https://bun.sh/install |
		env BUN_INSTALL="$PIFIRE_BUN_TMPDIR" BUN_INSTALL_CACHE_DIR="$PIFIRE_BUN_TMPDIR/cache" bash >>"$LOG" 2>&1; then
		pifire_report_no_bun "Could not download bun from https://bun.sh/install."
		return 1
	fi
	if [[ ! -x "$PIFIRE_BUN_TMPDIR/bin/bun" ]]; then
		pifire_report_no_bun "The bun installer ran but left nothing at $PIFIRE_BUN_TMPDIR/bin/bun."
		return 1
	fi
	PIFIRE_BUN="$PIFIRE_BUN_TMPDIR/bin/bun"
	# On PATH for this process only -- nothing is written outside the temp dir.
	# Required, not a convenience: package.json's "build" script is
	# `bun run typecheck && rsbuild build`, and that inner `bun` is resolved
	# through PATH by the shell bun spawns. Invoking the binary by absolute
	# path alone gets "bun: command not found" from inside its own script.
	export PATH="$PIFIRE_BUN_TMPDIR/bin:$PATH"
	log " + Temporary bun ready ($("$PIFIRE_BUN" --version 2>/dev/null))"
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

	# Not `$(pifire_get_bun)` -- that subshell would take the temp dir and the
	# cleanup trap with it. See PIFIRE_BUN.
	pifire_get_bun || return 1

	log " + Installing web UI dependencies"
	# `set -o pipefail` inside each subshell is load-bearing: without it the
	# pipeline's status is TEE's, so a failed install or build reports success
	# and the only thing that notices is the artifact check below. (The same
	# trap is documented at the uv sync in updater/upgrade.sh.)
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

	pifire_cleanup_bun
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
