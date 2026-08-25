#!/usr/bin/env bash

PIFIRE_NATIVE_FLOW_TEST="${PIFIRE_TEST_NATIVE_INSTALL_FLOW:-0}"
if [[ "$PIFIRE_NATIVE_FLOW_TEST" == "1" ]]; then
	PIFIRE_ENTRY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	: "${LOG:=/dev/null}"
	# shellcheck source=pifire-install-common.sh
	source "$PIFIRE_ENTRY_DIR/pifire-install-common.sh"
else

# PiFire Automatic Installation Script -- Fedora (x86_64)
#
# Companion to auto-install/install.sh (which targets Raspberry Pi OS / Debian on
# ARM). This script targets *Fedora on x86_64* hardware -- i.e. running PiFire on a
# regular PC/server with an external IO board rather than a Raspberry Pi's GPIO.
#
# Differences from the Debian/Pi installer:
#   * dnf instead of apt, with Fedora package names
#   * no Raspberry Pi specifics (rpi.gpio, rpi-lgpio, vcgencmd, Pi 5 checks)
#   * Fedora nginx layout (/etc/nginx/conf.d) and supervisor layout
#     (supervisord service + /etc/supervisord.d/*.ini)
#   * handles SELinux (httpd_can_network_connect) and firewalld
#   * MS core fonts (trebuchet/impact, used by the display modules) are installed
#     best-effort since they are not in the Fedora repos
#
# Install with this command (from your Fedora machine):
#   curl https://raw.githubusercontent.com/dberlin/pifire/massive-reworks-and-new-ui/auto-install/install-fedora.sh | bash
#
# Usage:
#   ./install-fedora.sh [-dev]
#     -dev   Install PiFire from the development branch instead of main.

set -o pipefail

INSTALL_SCRIPT_VERSION="1.10.x-fedora"

# --- Branch selection ------------------------------------------------------
DEV_REPO="false"
for arg in "$@"; do
	if [[ "$arg" == "-dev" || "$arg" == "-devrepo" ]]; then
		DEV_REPO="true"
	fi
done

# --- Logging ---------------------------------------------------------------
mkdir -p ~/logs
LOG=~/logs/pifire_install.log
log() { echo "$@" | tee -a "$LOG"; }

echo "*************************************************************************" | tee "$LOG"
log "PiFire Fedora Installation (v$INSTALL_SCRIPT_VERSION) started at $(date '+%Y-%m-%d %H:%M:%S')"
log " ** Logging to $LOG **"
echo "*************************************************************************" | tee -a "$LOG"

# Read interactive answers from the terminal even when run via 'curl | bash'.
ask() { # ask "prompt" "default" -> echoes the answer
	local prompt="$1" default="$2" reply=""
	if [[ -r /dev/tty ]]; then
		read -r -p "$prompt" reply </dev/tty || reply=""
	fi
	echo "${reply:-$default}"
}

# --- Root / sudo -----------------------------------------------------------
if [[ $EUID -eq 0 ]]; then
	log " + You are root."
	SUDO=""
else
	if ! command -v sudo >/dev/null 2>&1; then
		log " !! 'sudo' not found. Install it (dnf install sudo) and re-run. Exiting."
		exit 1
	fi
	SUDO="sudo"
	log " + SUDO will be used for the install. Please authenticate."
	sudo -v || {
		log " !! Failed to authenticate with sudo. Exiting."
		exit 1
	}
	# Keep the sudo timestamp fresh for the duration of the install.
	while true; do
		sudo -n true
		sleep 60
		kill -0 "$$" 2>/dev/null || exit
	done 2>/dev/null &
	SUDO_KEEPALIVE_PID=$!
fi

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
	log ""
	log " !! ====================================================================="
	log " !! Required command(s) not found on PATH: ${PIFIRE_MISSING[*]}"
	log " !! ====================================================================="
	log " !! Nothing has been changed."
	log " !!"
	log " !! If those are user-management tools (groupadd/usermod/useradd), they"
	log " !! are in /usr/sbin, which a normal user's PATH omits. Do NOT re-run"
	log " !! this installer as root -- it uses sudo where it needs to, and"
	log " !! running the whole thing as root leaves the PiFire checkout owned by"
	log " !! root rather than by you. Add sbin to PATH for this run instead:"
	log " !!"
	log " !!     PATH=\"/usr/local/sbin:/usr/sbin:/sbin:$PATH\" bash install-fedora.sh"
	log " !!"
	log " !! Anything else listed is genuinely missing; install it first."
	log " !! ====================================================================="
	exit 1
fi

# --- OS / architecture sanity ---------------------------------------------
ARCH=$(uname -m)
log " + Detecting architecture: $ARCH"
if [[ "$ARCH" != "x86_64" ]]; then
	log " !! This Fedora installer targets x86_64. Detected '$ARCH'."
	ans=$(ask " ?? Continue anyway? [y/N] " "N")
	[[ "$ans" =~ ^[Yy] ]] || {
		log " !! Aborting."
		exit 1
	}
fi

if [[ -f /etc/os-release ]]; then
	. /etc/os-release
	log " + Detected OS: $NAME $VERSION_ID (ID=$ID)"
	if [[ "$ID" != "fedora" ]]; then
		log " !! This installer is written for Fedora. Detected ID='$ID'."
		ans=$(ask " ?? Continue anyway? [y/N] " "N")
		[[ "$ans" =~ ^[Yy] ]] || {
			log " !! Aborting."
			exit 1
		}
	fi
else
	log " !! /etc/os-release not found; cannot verify OS. Exiting."
	exit 1
fi

# --- Supervisor WebUI option ----------------------------------------------
SVISOR="DISABLE_SVISOR"
USERNAME=""
PASSWORD=""
ans=$(ask " ?? Enable the Supervisor WebUI (process status/restart at :9001)? [y/N] " "N")
if [[ "$ans" =~ ^[Yy] ]]; then
	SVISOR="ENABLE_SVISOR"
	USERNAME=$(ask " -> Supervisor WebUI username [user]: " "user")
	PASSWORD=$(ask " -> Supervisor WebUI password [pifire]: " "pifire")
	log " + Supervisor WebUI will be enabled on port 9001 for user '$USERNAME'."
else
	log " + Supervisor WebUI disabled."
fi

# --- System update ---------------------------------------------------------
log "*************************************************************************"
log "**  Running dnf upgrade... (this can take several minutes)             **"
log "*************************************************************************"
$SUDO dnf -y upgrade --refresh 2>&1 | tee -a "$LOG"

# --- Dependencies ----------------------------------------------------------
log "*************************************************************************"
log "**  Installing dependencies...                                        **"
log "*************************************************************************"
# Build toolchain + scientific libraries (scipy), web stack, supervisor,
# bluetooth, image libs, and DejaVu fonts.
$SUDO dnf -y install \
	python3 python3-devel python3-pip python3-scipy \
	gcc-gfortran openblas-devel lapack-devel \
	openjpeg-devel glib2-devel \
	libjpeg-turbo-devel zlib-ng-compat-devel freetype-devel lcms2-devel libtiff-devel libwebp-devel \
	nginx git supervisor sway seatd \
	nodejs \
	bluez bluez-libs-devel \
	cabextract curl unzip dejavu-sans-fonts fontconfig 2>&1 | tee -a "$LOG"
if [ ${PIPESTATUS[0]} -ne 0 ]; then
	log " !! Failed to install dependencies. Installation cannot continue."
	exit 1
fi

# Microsoft core fonts (trebuc.ttf / impact.ttf, used by the display modules) are
# not in the Fedora repos. Install them best-effort from the standard installer
# RPM. If it fails (e.g. no network), the display falls back to DejaVu fonts; you
# can set the display 'primary_font' to 'DejaVuSans.ttf' in that case.
log " + Installing Microsoft core fonts (best-effort)..."
if $SUDO dnf -y install https://downloads.sourceforge.net/project/mscorefonts2/rpms/msttcore-fonts-installer-2.6-1.noarch.rpm 2>&1 | tee -a "$LOG"; then
	$SUDO fc-cache -f 2>&1 | tee -a "$LOG"
	log " + MS core fonts installed."
else
	log " ! Could not install MS core fonts; displays will fall back to DejaVu."
fi

# Unblock Bluetooth (if blocked) and enable the bluetooth service.
log " + Enabling Bluetooth"
command -v rfkill >/dev/null 2>&1 && [ -e /dev/rfkill ] && $SUDO rfkill unblock bluetooth 2>&1 | tee -a "$LOG"
$SUDO systemctl enable --now bluetooth 2>&1 | tee -a "$LOG" || log " ! bluetooth service not enabled (continuing)."

# --- Clone PiFire ----------------------------------------------------------
log "*************************************************************************"
log "**  Cloning PiFire from GitHub...                                     **"
log "*************************************************************************"
PIFIRE_REPO_URL="https://github.com/dberlin/pifire"
PIFIRE_BRANCH="massive-reworks-and-new-ui"
cd /usr/local/bin
if [[ ! -d /usr/local/bin/pifire ]]; then
	log " + Cloning $PIFIRE_BRANCH branch..."
	# --progress: the pipe into tee makes git think it has no terminal, and it
	# then stays silent for the whole download. This is the slowest step in the
	# install, so it is the one that most needs to show where it has got to.
	$SUDO git clone --progress --branch "$PIFIRE_BRANCH" "$PIFIRE_REPO_URL" 2>&1 | tee -a "$LOG"
elif [[ ! -d /usr/local/bin/pifire/.git ]]; then
	log " !! /usr/local/bin/pifire exists but is not a git checkout."
	log " !! Move it aside and re-run -- this installer will not overwrite it."
	exit 1
else
	# An existing checkout is brought to the branch tip, never assumed to be
	# at it. Left in place, one from an earlier release goes on to source a
	# shared library that release did not ship, and every call into it fails
	# as "command not found" while the install carries on regardless.
	branch="$($SUDO git -C /usr/local/bin/pifire rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
	if [[ "$branch" != "$PIFIRE_BRANCH" ]]; then
		log " !! /usr/local/bin/pifire is on '$branch', not '$PIFIRE_BRANCH'."
		log " !! Switch it or move it aside, then re-run. Nothing was changed."
		exit 1
	fi
	log " + Updating the existing checkout to the tip of $PIFIRE_BRANCH"
	if ! (
		set -o pipefail
		$SUDO git -C /usr/local/bin/pifire fetch --progress origin "$PIFIRE_BRANCH" 2>&1 | tee -a "$LOG"
	); then
		log " !! Could not fetch $PIFIRE_BRANCH. Check the network and re-run."
		exit 1
	fi
	# --ff-only, not reset --hard: an update must not silently discard work
	# somebody has in that directory.
	if ! (
		set -o pipefail
		$SUDO git -C /usr/local/bin/pifire merge --ff-only FETCH_HEAD 2>&1 | tee -a "$LOG"
	); then
		log " !! /usr/local/bin/pifire cannot be fast-forwarded -- it has local"
		log " !! commits or modified files an update would lose. Nothing has been"
		log " !! changed there. Resolve it (git -C /usr/local/bin/pifire status)"
		log " !! or move the directory aside, then re-run."
		exit 1
	fi
fi

# Shared install steps (device access, web UI build). Sourced rather than
# inlined now that the repo is on disk -- see auto-install/pifire-install-common.sh.
PIFIRE_COMMON=/usr/local/bin/pifire/auto-install/pifire-install-common.sh
if [[ ! -r "$PIFIRE_COMMON" ]]; then
	# Fatal, and deliberately so: sourcing a missing file only warns, and the
	# install then runs to completion with every shared step silently skipped.
	log " !! $PIFIRE_COMMON is missing or unreadable."
	log " !! Nothing after this point would work. Aborting."
	exit 1
fi
# shellcheck source=pifire-install-common.sh
source "$PIFIRE_COMMON"


# --- pifire group / ownership / sudoers -----------------------------------
log " + Setting up the pifire group and permissions"

# Seat access for the sway Wayland compositor (QtQuick displays).
$SUDO systemctl enable --now seatd 2>&1 | tee -a "$LOG" || log " ! seatd not enabled (continuing)."

pifire_add_hardware_groups "$USER" root
pifire_install_udev_rules /usr/local/bin/pifire

$SUDO chown -R "$USER":pifire /usr/local/bin/pifire
$SUDO chmod -R 775 /usr/local/bin/pifire
# After the recursive chmod, which would otherwise drop the setgid bit.
pifire_prepare_log_dir /usr/local/bin/pifire "$USER"

# Sudoers drop-in so the pifire group can run the system commands PiFire needs
# without a password. Fedora paths / package manager (no Raspberry Pi vcgencmd).
log " + Installing sudoers rules for the pifire group"
$SUDO tee /etc/sudoers.d/pifire >/dev/null <<'EOF'
# Allow members of the pifire group to run the system commands PiFire needs
# without being prompted for a password (Fedora x86_64).

# System control (reboot/shutdown from the app)
%pifire ALL=(ALL) NOPASSWD: /usr/sbin/shutdown, /usr/sbin/reboot

# Supervisor management (common.py restarts control/webapp; Fedora unit is supervisord)
%pifire ALL=(ALL) NOPASSWD: /usr/bin/supervisorctl
%pifire ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart supervisord, /usr/bin/systemctl restart nginx

# Package management (updater.py / wizard.py installs)
%pifire ALL=(ALL) NOPASSWD: /usr/bin/dnf

# Bluetooth
%pifire ALL=(ALL) NOPASSWD: /usr/sbin/rfkill

# File operations (updater manifest copies config files)
%pifire ALL=(ALL) NOPASSWD: /usr/bin/cp

# Script execution (updater/wizard manifests run setup scripts, setcap, etc.)
%pifire ALL=(ALL) NOPASSWD: /usr/bin/bash

# board-config.py (wizard runs this via the venv python with sudo)
%pifire ALL=(ALL) NOPASSWD: /usr/local/bin/pifire/.venv/bin/python
EOF
$SUDO chmod 0440 /etc/sudoers.d/pifire
if ! $SUDO visudo -cf /etc/sudoers.d/pifire; then
	log " !! sudoers validation failed; removing the drop-in and continuing."
	$SUDO rm -f /etc/sudoers.d/pifire
else
	log " + sudoers rules installed."
fi

# --- Python venv (UV) + modules -------------------------------------------
log "*************************************************************************"
log "**  Setting up the Python venv (UV) and installing modules...         **"
log "*************************************************************************"
log " + Installing UV"
if ! /bin/curl -LsSf https://astral.sh/uv/install.sh 2>>"$LOG" | $SUDO env UV_INSTALL_DIR="/usr/local/bin" /bin/sh 2>&1 | tee -a "$LOG"; then
	log " !! Failed to install UV. Exiting."
	exit 1
fi

cd /usr/local/bin/pifire
log " + Creating venv (system-site-packages, for python3-scipy)"
# --allow-existing so a re-run reuses the venv instead of failing with
# "a virtual environment already exists" and carrying on regardless.
#
# Fatal: nothing runs under `set -e`, so an unchecked failure here is discarded,
# the activate below fails just as quietly, and the rest of the install runs
# against the system interpreter -- which on Fedora is externally managed, so it
# resurfaces as an unrelated pip/sync error rather than as the real cause. The
# `set -o pipefail` at the top of this script is what makes the status uv's and
# not tee's.
if ! uv venv --system-site-packages --allow-existing 2>&1 | tee -a "$LOG"; then
	log " !! Failed to create the Python venv. Installation cannot continue."
	exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# pyproject.toml + uv.lock are the single source of truth for Python
# dependencies. The old auto-install/requirements.txt has been removed: it had
# drifted from pyproject.toml in both directions, and the influxdb_client
# version installed here contradicted the bound pyproject declares. It now
# resolves from the lockfile like everything else; the influxdb [ciso] extra
# moved into pyproject so it is still applied.
fi
PIFIRE_NATIVE_REPO="${PIFIRE_TEST_REPO_ROOT:-/usr/local/bin/pifire}"
pifire_install_acados_prerequisites fedora
PIFIRE_NATIVE_CODE=$?
if [[ $PIFIRE_NATIVE_CODE -ne 0 ]]; then
	exit "$PIFIRE_NATIVE_CODE"
fi
pifire_sync_python_and_rebuild_acados "$PIFIRE_NATIVE_REPO"
PIFIRE_NATIVE_CODE=$?
if [[ $PIFIRE_NATIVE_CODE -ne 0 ]]; then
	exit "$PIFIRE_NATIVE_CODE"
fi
if [[ "$PIFIRE_NATIVE_FLOW_TEST" != "1" ]]; then


# Grant the BLE helper the capabilities it needs (best-effort).
BLUEPY_HELPERS=$(find /usr/local/bin/pifire/.venv/lib/ -path "*/bluepy/bluepy-helper" 2>/dev/null)
if [ -n "$BLUEPY_HELPERS" ]; then
	for helper in $BLUEPY_HELPERS; do
		log " + Setting capabilities on $helper"
		$SUDO setcap "cap_net_raw,cap_net_admin+eip" "$helper" && getcap "$helper" | tee -a "$LOG"
	done
fi

# Record installed packages and board/OS info for the app.
log " - Getting PIP list and OS info into JSON"
python updater.py --piplist 2>&1 | tee -a "$LOG"
python board-config.py -ov 2>&1 | tee -a "$LOG"

# --- Web UI ----------------------------------------------------------------
log "*************************************************************************"
log "**  Building the web UI...                                            **"
log "*************************************************************************"
# Fatal: web-react/dist is what Flask serves the whole interface from
# (blueprints/spa/routes.py) and is not checked in, so without this the install
# finishes "successfully" with nothing to browse to.
if ! pifire_build_web_ui /usr/local/bin/pifire; then
	log " !! The web UI could not be built, so PiFire would have no interface."
	log " !! See $LOG. Fix the cause and re-run, or build it by hand with:"
	log " !!     cd /usr/local/bin/pifire/web-react && bun install && bun run build"
	exit 1
fi
$SUDO chown -R "$USER":pifire /usr/local/bin/pifire/web-react

# --- nginx -----------------------------------------------------------------
log "*************************************************************************"
log "**  Configuring nginx...                                              **"
log "*************************************************************************"
$SUDO mkdir -p /etc/ssl/private /etc/ssl/certs
log " + Generating self-signed SSL certificate"
if ! $SUDO openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
	-keyout /etc/ssl/private/localhost.key -out /etc/ssl/certs/localhost.crt \
	-subj "/CN=localhost" -batch 2>&1 | tee -a "$LOG"; then
	log " ! SSL certificate generation failed; HTTPS may not work."
fi

# Drop the PiFire site into conf.d and make it the default server. Fedora ships a
# default server inside nginx.conf, so strip its default_server flag to avoid a
# duplicate-default conflict (PiFire then handles all unmatched requests).
log " + Installing PiFire nginx site (conf.d/pifire.conf)"
$SUDO sed -i 's/[[:space:]]*default_server//g' /etc/nginx/nginx.conf
$SUDO cp /usr/local/bin/pifire/auto-install/nginx/server_error.html /usr/share/nginx/html/server_error.html
$SUDO tee /etc/nginx/conf.d/pifire.conf >/dev/null <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    error_page 400 404 500 501 502 503 504 /server_error.html;
    location = /server_error.html { root /usr/share/nginx/html; internal; }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /socket.io {
        proxy_pass http://127.0.0.1:8000/socket.io;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
    }
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    http2 on;
    server_name _;

    ssl_certificate /etc/ssl/certs/localhost.crt;
    ssl_certificate_key /etc/ssl/private/localhost.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    error_page 400 404 500 501 502 503 504 /server_error.html;
    location = /server_error.html { root /usr/share/nginx/html; internal; }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /socket.io {
        proxy_pass http://127.0.0.1:8000/socket.io;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
    }
}
EOF

# SELinux: allow nginx to proxy to gunicorn on 127.0.0.1:8000.
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" != "Disabled" ]; then
	log " + SELinux is active; allowing httpd_can_network_connect"
	$SUDO setsebool -P httpd_can_network_connect 1 2>&1 | tee -a "$LOG"
fi

# firewalld: open http/https if firewalld is running.
if command -v firewall-cmd >/dev/null 2>&1 && $SUDO firewall-cmd --state >/dev/null 2>&1; then
	log " + Opening http/https in firewalld"
	$SUDO firewall-cmd --permanent --add-service=http 2>&1 | tee -a "$LOG"
	$SUDO firewall-cmd --permanent --add-service=https 2>&1 | tee -a "$LOG"
	$SUDO firewall-cmd --reload 2>&1 | tee -a "$LOG"
fi

log " + Testing and (re)starting nginx"
if $SUDO nginx -t 2>&1 | tee -a "$LOG"; then
	$SUDO systemctl enable nginx 2>&1 | tee -a "$LOG"
	$SUDO systemctl restart nginx 2>&1 | tee -a "$LOG"
else
	log " !! nginx config test failed; check $LOG."
fi

# --- supervisor ------------------------------------------------------------
log "*************************************************************************"
log "**  Configuring supervisord...                                        **"
log "*************************************************************************"
# Fedora's supervisor reads /etc/supervisord.d/*.ini. Reuse the repo's program
# definitions (the .venv/uv variant -- correct for this x86_64 install) and add
# the run-as user.
$SUDO mkdir -p /etc/supervisord.d
for prog in control webapp display; do
	tmp="/tmp/pifire-$prog.ini"
	cp "/usr/local/bin/pifire/auto-install/supervisor/$prog.conf" "$tmp"
	# display runs as root (like the Raspberry Pi installers) since root was
	# added to the video/input/render/seat groups above for sway/seatd access.
	if [ "$prog" != "display" ]; then
		echo "user=$USER" >>"$tmp"
	fi
	$SUDO cp "$tmp" "/etc/supervisord.d/$prog.ini"
	rm -f "$tmp"
done

if [[ "$SVISOR" == "ENABLE_SVISOR" ]]; then
	log " + Enabling the Supervisor WebUI on :9001"
	{
		echo ""
		echo "[inet_http_server]"
		echo "port = 9001"
		echo "username = $USERNAME"
		echo "password = $PASSWORD"
	} | $SUDO tee -a /etc/supervisord.conf >/dev/null
fi

log " + Enabling and starting supervisord"
$SUDO systemctl enable supervisord 2>&1 | tee -a "$LOG"
fi
$SUDO systemctl restart supervisord 2>&1 | tee -a "$LOG"
PIFIRE_SERVICE_CODE=${PIPESTATUS[0]}
if [[ "$PIFIRE_NATIVE_FLOW_TEST" == "1" ]]; then
	exit "$PIFIRE_SERVICE_CODE"
fi

# --- Done ------------------------------------------------------------------
log "*************************************************************************"
log "+ Installation completed at $(date '+%Y-%m-%d %H:%M:%S')"
log "  Open http://$(hostname -I 2>/dev/null | awk '{print $1}')/ (or https://) to reach PiFire."
log "*************************************************************************"
$SUDO cp "$LOG" "/usr/local/bin/pifire/logs/pifire_install_$(date '+%Y%m%d_%H%M%S').log" 2>/dev/null || true

ans=$(ask " ?? Reboot now to finish setup? [y/N] " "N")
if [[ "$ans" =~ ^[Yy] ]]; then
	log " + Rebooting..."
	$SUDO reboot
else
	log " + Reboot skipped. Reboot manually when convenient."
fi

exit 0
