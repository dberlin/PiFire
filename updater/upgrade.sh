#!/usr/bin/env bash

# Upgrade Installation Script
# Many thanks to the PiVPN project (pivpn.io) for much of the inspiration for this script
# Run from https://raw.githubusercontent.com/nebhead/pifire/master/auto-install/upgrade.sh

echo "Starting PiFire Upgrade Script..." | tee -a /usr/local/bin/pifire/logs/upgrade.log

# Must be root to install
if [[ $EUID -eq 0 ]]; then
	echo "You are root." | tee -a /usr/local/bin/pifire/logs/upgrade.log
else
	echo "SUDO will be used for the install." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	# Check if it is actually installed
	# If it isn't, exit because the install cannot complete
	if [[ $(dpkg-query -s sudo) ]]; then
		export SUDO="sudo"
		export SUDOE="sudo -E"
	else
		echo "Please install sudo. Exiting." | tee -a /usr/local/bin/pifire/logs/upgrade.log
		exit 1
	fi
fi

export HOME=$(eval echo ~${SUDO_USER})

# Detect OS architecture
ARCH=$(/bin/uname -m)
echo " + Detecting system architecture: $ARCH" | tee -a /usr/local/bin/pifire/logs/upgrade.log

case $ARCH in
aarch64)
	echo " + 64-bit ARM OS detected (Raspberry Pi running 64-bit OS)" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	OS_BITS="64"
	;;
armv7l | armv6l)
	echo " + 32-bit ARM OS detected (Raspberry Pi running 32-bit OS)" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	OS_BITS="32"
	;;
*)
	echo " !! Warning: Non-standard Raspberry Pi architecture detected: $ARCH" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! This script is designed for Raspberry Pi systems" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	;;
esac
echo " + System architecture set to: $OS_BITS-bit" | tee -a /usr/local/bin/pifire/logs/upgrade.log

# 32-bit is not supported and cannot be made to work here: pyproject.toml sets
# requires-python = ">=3.14" and no 32-bit Raspberry Pi OS ships a 3.14, while
# the pip/`bin/` venv path that used to serve armv7 has been removed. Bail out
# *now*, before the groupadd/usermod/chown/chmod below: this script is run
# unattended by the web updater (see updater/updater_manifest.json, build 34),
# and continuing would fail at `uv sync` only after a supervisor config had
# already been written pointing at a venv that was never populated -- i.e. a
# half-upgraded, non-booting install.
if [ "$OS_BITS" = "32" ]; then
	echo "" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! =====================================================================" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! UPGRADE STOPPED: PiFire no longer supports 32-bit operating systems." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! =====================================================================" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! Detected architecture: $ARCH (32-bit)." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! This version of PiFire requires Python 3.14 or newer, which no" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! 32-bit Raspberry Pi OS release provides, so its Python dependencies" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! cannot be installed on this system." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! NOTHING HAS BEEN CHANGED. Your existing PiFire installation is" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! untouched and will keep running as before." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! Your options:" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!   1. Move to a 64-bit OS. Back up /usr/local/bin/pifire (at minimum" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!      pifire.db and the cookfiles/ directory), re-image the SD" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!      card with 64-bit Raspberry Pi OS -- supported on Pi 3, Pi 4," | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!      Pi 5 and Pi Zero 2 W -- run auto-install/install.sh, then" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!      restore your backup." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!   2. Stay where you are. Decline this upgrade and keep running your" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!      current PiFire version, which continues to work on 32-bit." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! A 32-bit-only board (Pi 2, Pi Zero W) cannot run a 64-bit OS and" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! would need replacing with a Pi Zero 2 W or newer to move forward." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! =====================================================================" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	exit 1
fi

#export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if ! command -v /bin/curl >/dev/null 2>&1; then
	echo "WARNING: curl is required but not found. Please install curl. Exiting" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	exit 1
fi

# Setup Python VENV & Install Python dependencies
echo " * Setting up Python VENV and Installing Modules..." | tee -a /usr/local/bin/pifire/logs/upgrade.log
sleep 1
echo " - Setting Up PiFire Group" | tee -a /usr/local/bin/pifire/logs/upgrade.log
cd /usr/local/bin
# -f -r: idempotent, and a SYSTEM group (GID below the login range). udev
# warns that device node ownership by non-system accounts is deprecated,
# and auto-install/udev/99-pifire.rules hands this group device nodes.
$SUDO groupadd -f -r pifire
USERNAME=$(id -un)
$SUDO usermod -a -G pifire $USERNAME
$SUDO usermod -a -G pifire root
# Change ownership to group=pifire for all files/directories in pifire
$SUDO chown -R $USERNAME:pifire pifire
# Change ability for pifire group to read/write/execute
$SUDO chmod -R 777 /usr/local/bin

echo " - Installing module dependencies... " | tee -a /usr/local/bin/pifire/logs/upgrade.log

# pyproject.toml + uv.lock are the single source of truth for Python
# dependencies. auto-install/requirements.txt is gone -- it had drifted from
# pyproject.toml in both directions -- and with it the 32-bit legacy-venv/pip
# branch, since uv installs from pyproject on every architecture. An install
# upgrading from a legacy `bin/` venv gets a fresh `.venv/` here.
echo " + Installing UV" | tee -a /usr/local/bin/pifire/logs/upgrade.log
if ! /bin/curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" /bin/sh; then
	echo "ERROR: Failed to download or install UV. Exiting." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	exit 1
fi

# Setup VENV
echo " + Setting up VENV" | tee -a /usr/local/bin/pifire/logs/upgrade.log
cd /usr/local/bin/pifire
# --allow-existing: on an upgrade the venv ALWAYS exists, so without this the
# command fails every single time and the upgrade continues past it.
uv venv --system-site-packages --allow-existing

# Activate VENV
source .venv/bin/activate

# --inexact so a conditionally-installed extra (e.g. rpi.gpio, which is skipped
# on a Pi 5 and so is deliberately not a pyproject dependency) is not pruned.
echo " + Installing module dependencies from pyproject.toml... " | tee -a /usr/local/bin/pifire/logs/upgrade.log
uv sync --no-dev --inexact 2>&1 | tee -a /usr/local/bin/pifire/logs/upgrade.log
# PIPESTATUS, not `if !`: no `set -o pipefail` here, so the pipeline status is
# tee's and a failed sync would be swallowed -- leaving an upgraded checkout
# pointed at a venv that never got its dependencies.
if [ ${PIPESTATUS[0]} -ne 0 ]; then
	echo " !! Failed to install Python dependencies. Upgrade cannot continue." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	exit 1
fi
echo " + Python dependency installation complete." | tee -a /usr/local/bin/pifire/logs/upgrade.log

# Find all bluepy-helper executables in various possible locations
BLUEPY_HELPERS=$(find /usr/local/bin/pifire/.venv/lib/ -path "*/bluepy/bluepy-helper" 2>/dev/null)

if [ -z "$BLUEPY_HELPERS" ]; then
	echo " ! No bluepy-helper found in the standard Python library locations" | tee -a /usr/local/bin/pifire/logs/upgrade.log
else
	# Apply capabilities to each found bluepy-helper
	for helper in $BLUEPY_HELPERS; do
		echo " + Setting capabilities for $helper" | tee -a /usr/local/bin/pifire/logs/upgrade.log
		$SUDO setcap "cap_net_raw,cap_net_admin+eip" "$helper"

		# Verify the capabilities were set
		getcap "$helper"
	done
	echo " + All bluepy-helper executables have been configured" | tee -a /usr/local/bin/pifire/logs/upgrade.log
fi

# Set the UV flag in the settings store (SQLite -- updater.py --uv writes
# globals.uv/venv/python_exec via write_settings()).
echo " - Setting UV flag in the settings database" | tee -a /usr/local/bin/pifire/logs/upgrade.log
python updater.py --uv

# Run wizard to update module dependencies
echo " - Running wizard to update module dependencies" | tee -a /usr/local/bin/pifire/logs/upgrade.log
python wizard.py --existing

# Get PIP List into JSON file
echo " - Getting PIP List into JSON file" | tee -a /usr/local/bin/pifire/logs/upgrade.log
python updater.py --piplist

# Get OS Information into JSON file
echo " - Getting OS Information into JSON file" | tee -a ~/logs/pifire_install.log
python board-config.py -ov 2>&1 | tee -a ~/logs/pifire_install.log

# Rebuild the web UI.
#
# web-react/dist is a build artifact and is git-ignored, so the update that
# just landed brought NEW React sources and left the OLD bundle in place. Every
# page would keep serving the previous release's UI -- against a backend that
# has already moved -- until someone rebuilt it by hand. Device access and
# group setup are refreshed here too, so an install predating those rules picks
# them up on upgrade rather than only on a reinstall.
echo " - Rebuilding the web UI" | tee -a /usr/local/bin/pifire/logs/upgrade.log
LOG=/usr/local/bin/pifire/logs/upgrade.log
PIFIRE_COMMON=/usr/local/bin/pifire/auto-install/pifire-install-common.sh
if [ ! -r "$PIFIRE_COMMON" ]; then
	# Fatal, and deliberately so: sourcing a missing file only warns, and the
	# upgrade then runs to completion with every shared step silently skipped
	# -- including the web UI build, leaving the previous bundle in place
	# against a backend that has moved.
	echo " !! $PIFIRE_COMMON is missing or unreadable." | tee -a "$LOG"
	echo " !! Nothing after this point would work. Aborting." | tee -a "$LOG"
	exit 1
fi
# shellcheck source=../auto-install/pifire-install-common.sh
source "$PIFIRE_COMMON"

pifire_add_hardware_groups "$USERNAME" root
pifire_install_udev_rules /usr/local/bin/pifire
# Repairs an install whose logs root already owns outright.
pifire_prepare_log_dir /usr/local/bin/pifire "$USERNAME"

if ! pifire_build_web_ui /usr/local/bin/pifire; then
	# Fatal, unlike most steps here: supervisor is restarted at the end of the
	# upgrade, and letting it come back against a bundle built from the
	# PREVIOUS release -- talking to a backend that just changed -- is worse
	# than stopping with the old version still running and a clear reason why.
	echo " !! Web UI build failed. The upgrade is incomplete." | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !! Rebuild it by hand and restart PiFire:" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	echo " !!   cd /usr/local/bin/pifire/web-react && bun install && bun run build" | tee -a /usr/local/bin/pifire/logs/upgrade.log
	exit 1
fi

### Setup Supervisor to Start Apps on Boot / Restart on Failures
echo " + Configuring Supervisord..." | tee -a /usr/local/bin/pifire/logs/upgrade.log

# Copy configuration files (control.conf, webapp.conf) to supervisor config
# directory. The supervisor/legacy/ variant pointed at the vanilla venv's
# python; with the uv venv the only interpreter is .venv/bin/python, so an
# upgrade from a legacy install is repointed here too.
echo " + Configuring supervisor for the uv venv" | tee -a /usr/local/bin/pifire/logs/upgrade.log
cd /usr/local/bin/pifire/auto-install/supervisor
# Add the current username to the configuration files
USERNAME=$(id -un)
echo "user=$USERNAME" | tee -a control.conf >/dev/null
echo "user=$USERNAME" | tee -a webapp.conf >/dev/null
$SUDO cp *.conf /etc/supervisor/conf.d/

echo " - Upgrade Script Finished." | tee -a /usr/local/bin/pifire/logs/upgrade.log
