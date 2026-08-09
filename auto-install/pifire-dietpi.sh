#!/usr/bin/env bash

# PiFire Automatic Installation Script for DietPi OS(Bookworm 64-Bit)
#
# !WARNING! This script is experimental! Use at your own risk.
#
# Many thanks to the PiVPN project (pivpn.io) for much of the inspiration for this script
# Run from https://raw.githubusercontent.com/dberlin/pifire/massive-reworks-and-new-ui/auto-install/pifire-dietpi.sh
#
# Install with this command (from your Pi):
#
# curl https://raw.githubusercontent.com/dberlin/pifire/massive-reworks-and-new-ui/auto-install/pifire-dietpi.sh | bash
#
# Pre-Requisites:
#       Do not run as ROOT (or with SUDO)
#       Enable WiFi or Ethernet
#       Complete initial OS installation
#       Use a non-root user for installation (such as the 'dietpi' user)
#
# Usage:
#       NOTE: DO NOT RUN AS ROOT (or with SUDO)
#       bash pifire-dietpi.sh [-dev]
#              -dev: Use this option to install the development branch of PiFire instead of the main branch.
#                    This is useful for testing new features or bug fixes that are not yet in the main branch.
#                    If this option is not used, the main branch will be installed by default.

# Create logs directory if it doesn't exist
mkdir -p ~/logs

# Log installation start time
echo "PiFire DietPi Installation Started at $(date '+%Y-%m-%d %H:%M:%S')" | tee ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo " ** Logging to ~/logs/pifire_install.log **" | tee -a ~/logs/pifire_install.log

echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "** Warning! This script is experimental! Use at your own risk.  *********" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log

# Must be root to install
if [[ $EUID -eq 0 ]]; then
	echo " + You are root." | tee -a ~/logs/pifire_install.log
else
	echo " + SUDO will be used for the install." | tee -a ~/logs/pifire_install.log
	# Check if it is actually installed
	# If it isn't, exit because the install cannot complete
	if [[ $(dpkg-query -s sudo) ]]; then
		export SUDO="sudo"
		export SUDOE="sudo -E"
	else
		echo " !! Installation Failed, 'sudo' not found. Please install sudo.  Exiting" | tee -a ~/logs/pifire_install.log
		exit 1
	fi
fi

# Find the rows and columns. Will default to 80x24 if it can not be detected.
screen_size=$(stty size 2>/dev/null || echo 24 80)
rows=$(echo $screen_size | awk '{print $1}')
columns=$(echo $screen_size | awk '{print $2}')
# Divide by two so the dialogs take up half of the screen.
r=$((rows / 2))
c=$((columns / 2))
# If the screen is small, modify defaults
r=$((r < 20 ? 20 : r))
c=$((c < 70 ? 70 : c))

# Detect OS architecture
# --- Required tools --------------------------------------------------------
# Checked before anything is installed, cloned or changed, so a PATH problem
# costs nothing but a message. groupadd/usermod/useradd live in /usr/sbin,
# which is on root's PATH but not on an ordinary user's -- and this script is
# meant to run as that ordinary user (it calls sudo itself).
PIFIRE_MISSING=()
for cmd in git curl groupadd usermod getent install udevadm; do
	command -v "$cmd" >/dev/null 2>&1 || PIFIRE_MISSING+=("$cmd")
done
if [[ ${#PIFIRE_MISSING[@]} -gt 0 ]]; then
	echo "" | tee -a ~/logs/pifire_install.log
	echo " !! =====================================================================" | tee -a ~/logs/pifire_install.log
	echo " !! Required command(s) not found on PATH: ${PIFIRE_MISSING[*]}" | tee -a ~/logs/pifire_install.log
	echo " !! =====================================================================" | tee -a ~/logs/pifire_install.log
	echo " !! Nothing has been changed." | tee -a ~/logs/pifire_install.log
	echo " !!" | tee -a ~/logs/pifire_install.log
	echo " !! If those are user-management tools (groupadd/usermod/useradd), they" | tee -a ~/logs/pifire_install.log
	echo " !! are in /usr/sbin, which a normal user's PATH omits. Do NOT re-run" | tee -a ~/logs/pifire_install.log
	echo " !! this installer as root -- it uses sudo where it needs to, and" | tee -a ~/logs/pifire_install.log
	echo " !! running the whole thing as root leaves the PiFire checkout owned by" | tee -a ~/logs/pifire_install.log
	echo " !! root rather than by you. Add sbin to PATH for this run instead:" | tee -a ~/logs/pifire_install.log
	echo " !!" | tee -a ~/logs/pifire_install.log
	echo " !!     PATH=\"/usr/local/sbin:/usr/sbin:/sbin:$PATH\" bash pifire-dietpi.sh" | tee -a ~/logs/pifire_install.log
	echo " !!" | tee -a ~/logs/pifire_install.log
	echo " !! Anything else listed is genuinely missing; install it first." | tee -a ~/logs/pifire_install.log
	echo " !! =====================================================================" | tee -a ~/logs/pifire_install.log
	exit 1
fi

ARCH=$(uname -m)
echo " + Detecting system architecture: $ARCH" | tee -a ~/logs/pifire_install.log

case $ARCH in
aarch64)
	echo " + 64-bit ARM OS detected" | tee -a ~/logs/pifire_install.log
	OS_BITS="64"
	;;
armv7l | armv6l)
	echo " + 32-bit ARM OS detected" | tee -a ~/logs/pifire_install.log
	OS_BITS="32"
	;;
*)
	echo " !! Warning: Non-standard Raspberry Pi architecture detected: $ARCH" | tee -a ~/logs/pifire_install.log
	echo " !! This script is designed for Raspberry Pi systems" | tee -a ~/logs/pifire_install.log
	if ! whiptail --backtitle "Architecture Warning" --title "Non-standard Architecture" --yesno "This script is designed for Raspberry Pi systems but detected architecture: $ARCH\n\nDo you want to continue anyway?" 12 60; then
		echo " !! Installation cancelled by user" | tee -a ~/logs/pifire_install.log
		exit 1
	fi
	;;
esac
echo " + System architecture set to: $OS_BITS-bit" | tee -a ~/logs/pifire_install.log

# 32-bit is not supported: pyproject.toml sets requires-python = ">=3.14" and no
# 32-bit DietPi/Raspberry Pi OS image ships a 3.14, while the pip/`bin/` venv
# path that used to serve armv7 has been removed. Fail here, before the first
# apt/git/groupadd, so a 32-bit box is left exactly as it was found rather than
# part-provisioned. Only the log directory (~/logs) has been created so far.
if [ "$OS_BITS" = "32" ]; then
	echo "" | tee -a ~/logs/pifire_install.log
	echo " !! =====================================================================" | tee -a ~/logs/pifire_install.log
	echo " !! INSTALL STOPPED: PiFire requires a 64-bit operating system." | tee -a ~/logs/pifire_install.log
	echo " !! =====================================================================" | tee -a ~/logs/pifire_install.log
	echo " !! Detected architecture: $ARCH (32-bit)." | tee -a ~/logs/pifire_install.log
	echo " !! PiFire requires Python 3.14 or newer, which no 32-bit DietPi image" | tee -a ~/logs/pifire_install.log
	echo " !! provides, so its Python dependencies cannot be installed on this" | tee -a ~/logs/pifire_install.log
	echo " !! system." | tee -a ~/logs/pifire_install.log
	echo " !!" | tee -a ~/logs/pifire_install.log
	echo " !! Nothing has been installed and no system files have been changed." | tee -a ~/logs/pifire_install.log
	echo " !!" | tee -a ~/logs/pifire_install.log
	echo " !! What to do:" | tee -a ~/logs/pifire_install.log
	echo " !!   * Re-image this SD card with the 64-bit (arm64) DietPi image and" | tee -a ~/logs/pifire_install.log
	echo " !!     run this installer again. 64-bit is supported on the Pi 3," | tee -a ~/logs/pifire_install.log
	echo " !!     Pi 4, Pi 5 and Pi Zero 2 W." | tee -a ~/logs/pifire_install.log
	echo " !!   * A 32-bit-only board (Pi 2, Pi Zero W, Pi 1) cannot run a 64-bit" | tee -a ~/logs/pifire_install.log
	echo " !!     OS and needs replacing with a Pi Zero 2 W or newer." | tee -a ~/logs/pifire_install.log
	echo " !! =====================================================================" | tee -a ~/logs/pifire_install.log
	exit 1
fi

sleep 2

# Display the welcome dialog
whiptail --msgbox --backtitle "Welcome" --title "PiFire Automated Installer" "This installer will transform your Single Board Computer into a connected Smoker Controller.  NOTE: This installer is intended to be run on a fresh install of DietPi OS 64-Bit Bookworm or later." ${r} ${c}

# Supervisor WebUI Settings
SVISOR=$(whiptail --title "Would you like to enable the supervisor WebUI?" --radiolist "This allows you to check the status of the supervised processes via a web browser, and also allows those processes to be restarted directly from this interface. (Recommended)" 20 78 2 "ENABLE_SVISOR" "Enable the WebUI" ON "DISABLE_SVISOR" "Disable the WebUI" OFF 3>&1 1>&2 2>&3)

if [[ $SVISOR = "ENABLE_SVISOR" ]]; then
	USERNAME=$(whiptail --inputbox "Choose a username [default: user]" 8 78 user --title "Choose Username" 3>&1 1>&2 2>&3)
	PASSWORD=$(whiptail --passwordbox "Enter your password" 8 78 --title "Choose Password" 3>&1 1>&2 2>&3)
	whiptail --msgbox --backtitle "Supervisor WebUI Setup" --title "Supervisor Configured" "After this installation is completed, you should be able to access the Supervisor WebUI at http://your.ip.address.here:9001 with the username and password you have chosen." ${r} ${c}
else
	echo "No Supervisor WebUI Setup." | tee -a ~/logs/pifire_install.log
fi

echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "**      Running Apt Update... (This could take several minutes)        **" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
$SUDO apt update 2>&1 | tee -a ~/logs/pifire_install.log

echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "**      Running Apt Upgrade... (This could take several minutes)       **" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
$SUDO DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
	-o Dpkg::Options::=--force-confdef \
	-o Dpkg::Options::=--force-confold 2>&1 | tee -a ~/logs/pifire_install.log

# Install APT dependencies
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "**      Installing Dependencies... (This could take several minutes)   **" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
$SUDO apt install python3-dev python3-pip python3-venv python3-scipy python3-rpi-lgpio nginx git supervisor nodejs ttf-mscorefonts-installer gfortran libatlas-base-dev libopenblas-dev liblapack-dev libopenjp2-7 libglib2.0-dev bluez bluez-firmware libnss-mdns sway seatd unzip -y 2>&1 | tee -a ~/logs/pifire_install.log

# Grab project files
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "**      Cloning PiFire from GitHub...                                  **" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
cd /usr/local/bin

PIFIRE_REPO_URL="https://github.com/dberlin/pifire"
PIFIRE_BRANCH="massive-reworks-and-new-ui"
if [[ ! -d /usr/local/bin/pifire ]]; then
	echo " + Cloning $PIFIRE_BRANCH branch..." | tee -a ~/logs/pifire_install.log
	# --progress: the pipe into tee makes git think it has no terminal, and it
	# then stays silent for the whole download. This is the slowest step in the
	# install, so it is the one that most needs to show where it has got to.
	$SUDO git clone --progress --branch "$PIFIRE_BRANCH" "$PIFIRE_REPO_URL" 2>&1 | tee -a ~/logs/pifire_install.log
elif [[ ! -d /usr/local/bin/pifire/.git ]]; then
	echo " !! /usr/local/bin/pifire exists but is not a git checkout." | tee -a ~/logs/pifire_install.log
	echo " !! Move it aside and re-run -- this installer will not overwrite it." | tee -a ~/logs/pifire_install.log
	exit 1
else
	# An existing checkout is brought to the branch tip, never assumed to be
	# at it. Left in place, one from an earlier release goes on to source a
	# shared library that release did not ship, and every call into it fails
	# as "command not found" while the install carries on regardless.
	branch="$($SUDO git -C /usr/local/bin/pifire rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
	if [[ "$branch" != "$PIFIRE_BRANCH" ]]; then
		echo " !! /usr/local/bin/pifire is on '$branch', not '$PIFIRE_BRANCH'." | tee -a ~/logs/pifire_install.log
		echo " !! Switch it or move it aside, then re-run. Nothing was changed." | tee -a ~/logs/pifire_install.log
		exit 1
	fi
	echo " + Updating the existing checkout to the tip of $PIFIRE_BRANCH" | tee -a ~/logs/pifire_install.log
	if ! (
		set -o pipefail
		$SUDO git -C /usr/local/bin/pifire fetch --progress origin "$PIFIRE_BRANCH" 2>&1 | tee -a ~/logs/pifire_install.log
	); then
		echo " !! Could not fetch $PIFIRE_BRANCH. Check the network and re-run." | tee -a ~/logs/pifire_install.log
		exit 1
	fi
	# --ff-only, not reset --hard: an update must not silently discard work
	# somebody has in that directory.
	if ! (
		set -o pipefail
		$SUDO git -C /usr/local/bin/pifire merge --ff-only FETCH_HEAD 2>&1 | tee -a ~/logs/pifire_install.log
	); then
		echo " !! /usr/local/bin/pifire cannot be fast-forwarded -- it has local" | tee -a ~/logs/pifire_install.log
		echo " !! commits or modified files an update would lose. Nothing has been" | tee -a ~/logs/pifire_install.log
		echo " !! changed there. Resolve it (git -C /usr/local/bin/pifire status)" | tee -a ~/logs/pifire_install.log
		echo " !! or move the directory aside, then re-run." | tee -a ~/logs/pifire_install.log
		exit 1
	fi
fi

# Setup Python VENV & Install Python dependencies
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "**      Setting up Python VENV and Installing Modules...               **" | tee -a ~/logs/pifire_install.log
echo "**            (This could take several minutes)                        **" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo ""
echo " + Setting Up PiFire Group"
cd /usr/local/bin
# -f -r: idempotent, and a SYSTEM group (GID below the login range). udev
# warns that device node ownership by non-system accounts is deprecated,
# and auto-install/udev/99-pifire.rules hands this group device nodes.
$SUDO groupadd -f -r pifire
$SUDO usermod -a -G pifire $USER
$SUDO usermod -a -G pifire root
# Change ownership to group=pifire for all files/directories in pifire
$SUDO chown -R $USER:pifire pifire
# Change ability for pifire group to read/write/execute
$SUDO chmod -R 777 /usr/local/bin

echo " + Setting permissions for interfaces"

# Shared install steps (device access, web UI build). Sourced rather than
# inlined now that the repo is on disk -- see auto-install/pifire-install-common.sh.
LOG=~/logs/pifire_install.log
PIFIRE_COMMON=/usr/local/bin/pifire/auto-install/pifire-install-common.sh
if [[ ! -r "$PIFIRE_COMMON" ]]; then
	# Fatal, and deliberately so: sourcing a missing file only warns, and the
	# install then runs to completion with every shared step silently skipped.
	echo " !! $PIFIRE_COMMON is missing or unreadable." | tee -a "$LOG"
	echo " !! Nothing after this point would work. Aborting." | tee -a "$LOG"
	exit 1
fi
# shellcheck source=pifire-install-common.sh
source "$PIFIRE_COMMON"
if ! pifire_install_acados_prerequisites debian; then
	exit 1
fi


# Seat access for the sway Wayland compositor (QtQuick displays).
$SUDO systemctl enable --now seatd 2>&1 | tee -a ~/logs/pifire_install.log

pifire_add_hardware_groups $USER root
pifire_install_udev_rules /usr/local/bin/pifire
# After the recursive chmod above, which would otherwise drop the setgid bit.
pifire_prepare_log_dir /usr/local/bin/pifire "$USER"

$SUDO " + Enabling and Starting Bluetooth service"
$SUDO systemctl enable bluetooth.service
$SUDO systemctl start bluetooth.service

# --- Python venv (uv) + modules -------------------------------------------
# pyproject.toml + uv.lock are the single source of truth for Python
# dependencies. The old auto-install/requirements.txt has been removed rather
# than re-synced by hand -- it had drifted from pyproject.toml in both
# directions -- and with it the separate 32-bit vanilla-venv/pip path, since
# uv installs from pyproject on every architecture. The influxdb [ciso] extra
# that used to be applied here now lives in pyproject.
echo " + Installing UV" | tee -a ~/logs/pifire_install.log
if ! /bin/curl -LsSf https://astral.sh/uv/install.sh | $SUDO env UV_INSTALL_DIR="/usr/local/bin" /bin/sh; then
	echo " ! Failed to download or install UV. Exiting." | tee -a ~/logs/pifire_install.log
	exit 1
fi

echo " + Setting up VENV" | tee -a ~/logs/pifire_install.log
cd /usr/local/bin/pifire
# --allow-existing so a re-run reuses the venv instead of failing with
# "a virtual environment already exists" and carrying on regardless.
uv venv --system-site-packages --allow-existing

# Activate VENV
source .venv/bin/activate

if ! pifire_sync_python_and_rebuild_acados /usr/local/bin/pifire; then
	exit 1
fi


# Find all bluepy-helper executables in various possible locations
BLUEPY_HELPERS=$(find /usr/local/bin/pifire/.venv/lib/ -path "*/bluepy/bluepy-helper" 2>/dev/null)

if [ -z "$BLUEPY_HELPERS" ]; then
	echo " ! No bluepy-helper found in the standard Python library locations" | tee -a ~/logs/pifire_install.log
else
	# Apply capabilities to each found bluepy-helper
	for helper in $BLUEPY_HELPERS; do
		echo " + Setting capabilities for $helper" | tee -a ~/logs/pifire_install.log
		$SUDO setcap "cap_net_raw,cap_net_admin+eip" "$helper"
	done
	echo " + All bluepy-helper executables have been configured" | tee -a ~/logs/pifire_install.log
fi

# Get PIP List into JSON file
echo " - Getting PIP List into JSON file" | tee -a ~/logs/pifire_install.log
python updater.py --piplist 2>&1 | tee -a ~/logs/pifire_install.log

# Get OS Information into JSON file
echo " - Getting OS Information into JSON file" | tee -a ~/logs/pifire_install.log
python board-config.py -ov 2>&1 | tee -a ~/logs/pifire_install.log

echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**      Building the web UI...                                         **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
# Fatal: web-react/dist is what Flask serves the whole interface from
# (blueprints/spa/routes.py) and is not checked in, so without this the install
# finishes "successfully" with nothing to browse to.
if ! pifire_build_web_ui /usr/local/bin/pifire; then
	echo " !! The web UI could not be built, so PiFire would have no interface." | tee -a ~/logs/pifire_install.log
	echo " !! Fix the cause and re-run, or build it by hand with:" | tee -a ~/logs/pifire_install.log
	echo " !!     cd /usr/local/bin/pifire/web-react && bun install && bun run build" | tee -a ~/logs/pifire_install.log
	exit 1
fi
$SUDO chown -R $USER:pifire /usr/local/bin/pifire/web-react

### Setup nginx to proxy to gunicorn
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "**      Configuring nginx...                                           **" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
# Move into install directory
cd /usr/local/bin/pifire/auto-install/nginx

# Delete default configuration
$SUDO rm /etc/nginx/sites-enabled/default

# Copy configuration file to nginx
$SUDO cp pifire.nginx /etc/nginx/sites-available/pifire

# Create link in sites-enabled
$SUDO ln -s /etc/nginx/sites-available/pifire /etc/nginx/sites-enabled

# Copy server_error.html to /usr/share/nginx/html
$SUDO cp server_error.html /usr/share/nginx/html

# Restart nginx
$SUDO service nginx restart

### Setup Supervisor to Start Apps on Boot / Restart on Failures
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "**      Configuring Supervisord...                                     **" | tee -a ~/logs/pifire_install.log
echo "**                                                                     **" | tee -a ~/logs/pifire_install.log
echo "*************************************************************************" | tee -a ~/logs/pifire_install.log

# Copy configuration files (control.conf, webapp.conf) to supervisor config
# directory. The supervisor/legacy/ variant pointed at the vanilla venv's
# python; with the uv venv the only interpreter is .venv/bin/python.
cd /usr/local/bin/pifire/auto-install/supervisor

# Add the current username to the configuration files
echo "user=$USER" | tee -a control.conf >/dev/null
echo "user=$USER" | tee -a webapp.conf >/dev/null

$SUDO cp *.conf /etc/supervisor/conf.d/

if [[ $SVISOR = "ENABLE_SVISOR" ]]; then
	echo " " | sudo tee -a /etc/supervisor/supervisord.conf >/dev/null
	echo "[inet_http_server]" | sudo tee -a /etc/supervisor/supervisord.conf >/dev/null
	echo "port = 9001" | sudo tee -a /etc/supervisor/supervisord.conf >/dev/null
	echo "username = " $USERNAME | sudo tee -a /etc/supervisor/supervisord.conf >/dev/null
	echo "password = " $PASSWORD | sudo tee -a /etc/supervisor/supervisord.conf >/dev/null
else
	echo "No WebUI Setup." | tee -a ~/logs/pifire_install.log
fi

# If supervisor isn't already running, startup Supervisor
$SUDO service supervisor start 2>&1 | tee -a ~/logs/pifire_install.log

# Installation Complete, Reboot Prompt
echo "+ Installation completed at $(date '+%Y-%m-%d %H:%M:%S')" | tee -a ~/logs/pifire_install.log

# Ask user if they want to reboot
if whiptail --backtitle "Install Complete" --title "Installation Completed" --yesno "Congratulations, the installation is complete.\n\nIt's recommended to reboot your system now for all changes to take effect. On first boot, the wizard will guide you through the remaining setup steps.\n\nYou should be able to access your application by opening a browser on your PC or other device and using the IP address (or http://[hostname].local) for this device.\n\nWould you like to reboot now?" ${r} ${c}; then
	echo "Rebooting system..." | tee -a ~/logs/pifire_install.log
	$SUDO cp ~/logs/pifire_install.log /usr/local/bin/pifire/logs/pifire_install_$(date '+%Y%m%d_%H%M%S').log
	$SUDO reboot
else
	echo "Reboot skipped. Please reboot manually when convenient." | tee -a ~/logs/pifire_install.log
	$SUDO cp ~/logs/pifire_install.log /usr/local/bin/pifire/logs/pifire_install_$(date '+%Y%m%d_%H%M%S').log
	exit 0
fi
