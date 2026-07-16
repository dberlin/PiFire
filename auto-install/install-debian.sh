#!/usr/bin/env bash

# PiFire Automatic Installation Script -- Debian / Ubuntu (x86_64)
#
# Companion to auto-install/install.sh (Raspberry Pi OS / Debian on ARM) and
# auto-install/install-fedora.sh (Fedora on x86_64). This script targets regular
# *Debian or Ubuntu on x86_64* hardware -- i.e. running PiFire on a normal PC or
# server with an external IO board rather than a Raspberry Pi's GPIO.
#
# Differences from the Raspberry Pi installer (install.sh):
#   * no Raspberry Pi specifics (rpi.gpio, rpi-lgpio, vcgencmd, Pi 5 checks,
#     armhf/aarch64 architecture handling)
#   * always uses the UV venv (.venv) path -- the 32-bit "legacy" venv layout is
#     Pi-only and not offered here
#   * x86-friendly extras: best-effort firewall (ufw) opening
#
# It keeps parity with install.sh where the platform is the same: apt package
# manager, the Debian nginx layout (sites-available/sites-enabled) and the Debian
# supervisor layout (/etc/supervisor/conf.d + the 'supervisor' service), reusing
# the repo's own nginx/supervisor config files.
#
# Install with this command (from your Debian/Ubuntu machine):
#   curl https://raw.githubusercontent.com/dberlin/pifire/massive-reworks-and-new-ui/auto-install/install-debian.sh | bash
#
# Usage:
#   ./install-debian.sh [-dev]
#     -dev   Install PiFire from the development branch instead of main.

set -o pipefail

INSTALL_SCRIPT_VERSION="1.10.x-debian"

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
log "PiFire Debian/Ubuntu Installation (v$INSTALL_SCRIPT_VERSION) started at $(date '+%Y-%m-%d %H:%M:%S')"
log " ** Logging to $LOG **"
echo "*************************************************************************" | tee -a "$LOG"

# Read interactive answers from the terminal even when run via 'curl | bash'.
ask() {  # ask "prompt" "default" -> echoes the answer
    local prompt="$1" default="$2" reply=""
    if [[ -r /dev/tty ]]; then
        read -r -p "$prompt" reply < /dev/tty || reply=""
    fi
    echo "${reply:-$default}"
}

# --- Root / sudo -----------------------------------------------------------
if [[ $EUID -eq 0 ]]; then
    log " + You are root."
    SUDO=""
else
    if ! command -v sudo >/dev/null 2>&1; then
        log " !! 'sudo' not found. Install it (apt install sudo) and re-run. Exiting."
        exit 1
    fi
    SUDO="sudo"
    log " + SUDO will be used for the install. Please authenticate."
    sudo -v || { log " !! Failed to authenticate with sudo. Exiting."; exit 1; }
    # Keep the sudo timestamp fresh for the duration of the install.
    while true; do sudo -n true; sleep 60; kill -0 "$$" 2>/dev/null || exit; done 2>/dev/null &
    SUDO_KEEPALIVE_PID=$!
fi

# --- OS / architecture sanity ---------------------------------------------
ARCH=$(uname -m)
log " + Detecting architecture: $ARCH"
if [[ "$ARCH" != "x86_64" ]]; then
    log " !! This Debian/Ubuntu installer targets x86_64. Detected '$ARCH'."
    log " !! For a Raspberry Pi (armhf/aarch64), use auto-install/install.sh instead."
    ans=$(ask " ?? Continue anyway? [y/N] " "N")
    [[ "$ans" =~ ^[Yy] ]] || { log " !! Aborting."; exit 1; }
fi

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_VERSION="$VERSION_ID"
    log " + Detected OS: $NAME $VERSION_ID (ID=$ID)"
    if [[ "$ID" != "debian" && "$ID" != "ubuntu" && "$ID_LIKE" != *debian* ]]; then
        log " !! This installer is written for Debian/Ubuntu. Detected ID='$ID'."
        ans=$(ask " ?? Continue anyway? [y/N] " "N")
        [[ "$ans" =~ ^[Yy] ]] || { log " !! Aborting."; exit 1; }
    fi
else
    log " !! /etc/os-release not found; cannot verify OS. Exiting."
    exit 1
fi

# --- Supervisor WebUI option ----------------------------------------------
SVISOR="DISABLE_SVISOR"; USERNAME=""; PASSWORD=""
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
log "**  Running apt update/upgrade... (this can take several minutes)      **"
log "*************************************************************************"
$SUDO apt update 2>&1 | tee -a "$LOG" || { log " !! apt update failed. Exiting."; exit 1; }
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold 2>&1 | tee -a "$LOG"
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log " !! Failed to upgrade packages. Installation cannot continue."
    exit 1
fi

# --- Dependencies ----------------------------------------------------------
log "*************************************************************************"
log "**  Installing dependencies...                                        **"
log "*************************************************************************"
# Build toolchain + scientific libs (scipy/scikit-learn), web stack, supervisor,
# bluetooth, and image libs. No Raspberry Pi packages.
$SUDO apt install -y \
    python3-dev python3-pip python3-venv python3-scipy \
    gfortran libopenblas-dev liblapack-dev libopenjp2-7-dev libglib2.0-dev \
    libjpeg-dev zlib1g-dev libfreetype-dev liblcms2-dev libtiff-dev libwebp-dev \
    nginx git supervisor \
    bluetooth bluez cage seatd wlr-randr \
    openssl curl 2>&1 | tee -a "$LOG"
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log " !! Failed to install dependencies. Installation cannot continue."
    exit 1
fi

# Older Debian releases (bullseye/bookworm) still ship libatlas-base-dev, which
# speeds up numpy/scipy. It was dropped in trixie, so install it best-effort.
if [[ "$OS_VERSION" == "11" || "$OS_VERSION" == "12" ]]; then
    log " + Installing libatlas-base-dev (OS version $OS_VERSION)"
    $SUDO apt install -y libatlas-base-dev 2>&1 | tee -a "$LOG" \
        || log " ! libatlas-base-dev not installed (continuing)."
fi

# Microsoft core fonts (trebuc.ttf / impact.ttf, used by the display modules) live
# in Debian's 'contrib' component and require accepting the EULA. Install them
# best-effort; if it fails the displays fall back to DejaVu, so make sure that is
# present too.
log " + Installing fonts (DejaVu + best-effort MS core fonts)"
$SUDO apt install -y fonts-dejavu-core 2>&1 | tee -a "$LOG" \
    || log " ! fonts-dejavu-core not installed (continuing)."
echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" \
    | $SUDO debconf-set-selections 2>/dev/null || true
if $SUDO env DEBIAN_FRONTEND=noninteractive apt install -y ttf-mscorefonts-installer 2>&1 | tee -a "$LOG"; then
    command -v fc-cache >/dev/null 2>&1 && $SUDO fc-cache -f 2>&1 | tee -a "$LOG"
    log " + MS core fonts installed."
else
    log " ! Could not install MS core fonts (needs the 'contrib' component);"
    log "   displays will fall back to DejaVu. Set display 'primary_font' to"
    log "   'DejaVuSans.ttf' in the settings if text does not render."
fi

# Unblock Bluetooth (if blocked) and enable the bluetooth service.
log " + Enabling Bluetooth"
command -v rfkill >/dev/null 2>&1 && $SUDO rfkill unblock bluetooth 2>&1 | tee -a "$LOG"
$SUDO systemctl enable --now bluetooth 2>&1 | tee -a "$LOG" || log " ! bluetooth service not enabled (continuing)."

# --- Clone PiFire ----------------------------------------------------------
log "*************************************************************************"
log "**  Cloning PiFire from GitHub...                                     **"
log "*************************************************************************"
cd /usr/local/bin
if [[ -d /usr/local/bin/pifire ]]; then
    log " ! /usr/local/bin/pifire already exists; leaving it in place."
else
    log " + Cloning massive-reworks-and-new-ui branch..."
    $SUDO git clone --depth 1 --branch massive-reworks-and-new-ui https://github.com/dberlin/pifire 2>&1 | tee -a "$LOG"
fi

# --- pifire group / ownership / sudoers -----------------------------------
log " + Setting up the pifire group and permissions"
$SUDO groupadd -f pifire
$SUDO usermod -a -G pifire "$USER"
$SUDO usermod -a -G pifire root

# Seat access for the cage Wayland compositor (QtQuick displays).
$SUDO systemctl enable --now seatd 2>&1 | tee -a "$LOG" || log " ! seatd not enabled (continuing)."
for grp in video input render seat; do
    $SUDO usermod -a -G "$grp" "$USER" 2>/dev/null || true
    $SUDO usermod -a -G "$grp" root 2>/dev/null || true
done

$SUDO chown -R "$USER":pifire /usr/local/bin/pifire
$SUDO chmod -R 775 /usr/local/bin/pifire

# Sudoers drop-in so the pifire group can run the system commands PiFire needs
# without a password. Debian paths / apt package manager (no Raspberry Pi vcgencmd).
log " + Installing sudoers rules for the pifire group"
$SUDO tee /etc/sudoers.d/pifire > /dev/null <<'EOF'
# Allow members of the pifire group to run the system commands PiFire needs
# without being prompted for a password (Debian/Ubuntu x86_64).

# System control (reboot/shutdown from the app)
%pifire ALL=(ALL) NOPASSWD: /sbin/shutdown, /sbin/reboot, /usr/sbin/shutdown, /usr/sbin/reboot

# Supervisor management (common.py restarts control/webapp; Debian unit is supervisor)
%pifire ALL=(ALL) NOPASSWD: /usr/bin/supervisorctl, /bin/supervisorctl
%pifire ALL=(ALL) NOPASSWD: /bin/systemctl restart supervisor, /usr/bin/systemctl restart supervisor
%pifire ALL=(ALL) NOPASSWD: /bin/systemctl restart nginx, /usr/bin/systemctl restart nginx

# Package management (updater.py / wizard.py installs)
%pifire ALL=(ALL) NOPASSWD: /usr/bin/apt, /usr/bin/apt-get

# Bluetooth
%pifire ALL=(ALL) NOPASSWD: /usr/bin/rfkill, /usr/sbin/rfkill

# File operations (updater manifest copies config files)
%pifire ALL=(ALL) NOPASSWD: /bin/cp, /usr/bin/cp

# Script execution (updater/wizard manifests run setup scripts, setcap, etc.)
%pifire ALL=(ALL) NOPASSWD: /bin/bash, /usr/bin/bash

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
if ! /bin/curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" /bin/sh 2>&1 | tee -a "$LOG"; then
    log " !! Failed to install UV. Exiting."
    exit 1
fi

cd /usr/local/bin/pifire
log " + Creating venv (system-site-packages, for python3-scipy)"
uv venv --system-site-packages 2>&1 | tee -a "$LOG"
# shellcheck disable=SC1091
source .venv/bin/activate

log " + Installing influxdb_client[ciso]==1.48.0"
uv pip install "influxdb_client[ciso]==1.48.0" 2>&1 | tee -a "$LOG" || { log " !! influxdb_client install failed. Exiting."; exit 1; }

# Match the Raspberry Pi installer's scikit-learn pins: older Debian toolchains
# (bullseye/bookworm) use 1.4.2, newer releases use 1.7.2.
if [[ "$OS_VERSION" == "11" || "$OS_VERSION" == "12" ]]; then
    SKLEARN_VERSION="1.4.2"
else
    SKLEARN_VERSION="1.7.2"
fi
log " + Installing scikit-learn==$SKLEARN_VERSION"
uv pip install "scikit-learn==$SKLEARN_VERSION" 2>&1 | tee -a "$LOG" \
    || { log " !! scikit-learn install failed. Exiting."; exit 1; }

log " + Installing modules from requirements.txt (one at a time)"
while IFS= read -r req || [ -n "$req" ]; do
    req="${req%%#*}"; req="$(echo "$req" | xargs)"
    [ -z "$req" ] && continue
    case "$req" in
        -r*|--requirement*|--find-links*|-f*|--index-url*|--extra-index-url*|--trusted-host*|--no-binary*|--only-binary*|--*)
            log " - Skipping requirement option: $req"; continue ;;
    esac
    log " - Installing $req ..."
    uv pip install "$req" 2>&1 | tee -a "$LOG"
    if [ ${PIPESTATUS[0]} -ne 0 ]; then
        log " !! Failed to install $req. Installation cannot continue."
        exit 1
    fi
done < /usr/local/bin/pifire/auto-install/requirements.txt
log " + requirements.txt installation complete."

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

# Debian nginx uses sites-available/sites-enabled. Remove the default site and
# install the repo's PiFire site (reused from the Raspberry Pi installer).
cd /usr/local/bin/pifire/auto-install/nginx
$SUDO rm -f /etc/nginx/sites-enabled/default
$SUDO cp pifire.nginx /etc/nginx/sites-available/pifire
$SUDO ln -sf /etc/nginx/sites-available/pifire /etc/nginx/sites-enabled/pifire
$SUDO cp server_error.html /usr/share/nginx/html/ 2>/dev/null \
    || { $SUDO mkdir -p /var/www/html && $SUDO cp server_error.html /var/www/html/; }

# firewalld/ufw: open http/https if a firewall is active (best-effort).
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
    log " + Opening http/https in ufw"
    $SUDO ufw allow 80/tcp 2>&1 | tee -a "$LOG"
    $SUDO ufw allow 443/tcp 2>&1 | tee -a "$LOG"
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
# Debian's supervisor reads /etc/supervisor/conf.d/*.conf. Reuse the repo's
# program definitions (the .venv/uv variant -- correct for this x86_64 install)
# and add the run-as user.
cd /usr/local/bin/pifire/auto-install/supervisor
echo "user=$USER" | tee -a control.conf > /dev/null
echo "user=$USER" | tee -a webapp.conf > /dev/null
$SUDO cp control.conf webapp.conf display.conf /etc/supervisor/conf.d/

if [[ "$SVISOR" == "ENABLE_SVISOR" ]]; then
    log " + Enabling the Supervisor WebUI on :9001"
    {
        echo ""
        echo "[inet_http_server]"
        echo "port = 9001"
        echo "username = $USERNAME"
        echo "password = $PASSWORD"
    } | $SUDO tee -a /etc/supervisor/supervisord.conf > /dev/null
fi

log " + Enabling and starting supervisor"
$SUDO systemctl enable supervisor 2>&1 | tee -a "$LOG"
$SUDO systemctl restart supervisor 2>&1 | tee -a "$LOG" \
    || $SUDO service supervisor restart 2>&1 | tee -a "$LOG"

# --- Done ------------------------------------------------------------------
log "*************************************************************************"
log "+ Installation completed at $(date '+%Y-%m-%d %H:%M:%S')"
log "  Open http://$(hostname -I 2>/dev/null | awk '{print $1}')/ (or https://) to reach PiFire."
log "  On first boot the wizard guides you through the remaining setup steps."
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
