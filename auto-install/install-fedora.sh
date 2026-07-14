#!/usr/bin/env bash

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
        log " !! 'sudo' not found. Install it (dnf install sudo) and re-run. Exiting."
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
    log " !! This Fedora installer targets x86_64. Detected '$ARCH'."
    ans=$(ask " ?? Continue anyway? [y/N] " "N")
    [[ "$ans" =~ ^[Yy] ]] || { log " !! Aborting."; exit 1; }
fi

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    log " + Detected OS: $NAME $VERSION_ID (ID=$ID)"
    if [[ "$ID" != "fedora" ]]; then
        log " !! This installer is written for Fedora. Detected ID='$ID'."
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
log "**  Running dnf upgrade... (this can take several minutes)             **"
log "*************************************************************************"
$SUDO dnf -y upgrade --refresh 2>&1 | tee -a "$LOG"

# --- Dependencies ----------------------------------------------------------
log "*************************************************************************"
log "**  Installing dependencies...                                        **"
log "*************************************************************************"
# Build toolchain + scientific libs (scipy/scikit-learn), web stack, supervisor,
# bluetooth, image libs, and DejaVu fonts.
$SUDO dnf -y install \
    python3 python3-devel python3-pip python3-scipy \
    gcc gcc-c++ make gcc-gfortran openblas-devel lapack-devel \
    openjpeg-devel glib2-devel \
    libjpeg-turbo-devel zlib-ng-compat-devel freetype-devel lcms2-devel libtiff-devel libwebp-devel \
    nginx git supervisor cage seatd wlr-randr \
    bluez bluez-libs-devel \
    cabextract curl dejavu-sans-fonts fontconfig 2>&1 | tee -a "$LOG"
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
cd /usr/local/bin
if [[ -d /usr/local/bin/pifire ]]; then
    log " ! /usr/local/bin/pifire already exists; leaving it in place."
elif [[ "$DEV_REPO" == "true" ]]; then
    log " + Cloning massive-reworks-and-new-ui branch..."
    $SUDO git clone --depth 1 --branch massive-reworks-and-new-ui https://github.com/dberlin/pifire 2>&1 | tee -a "$LOG"
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
# without a password. Fedora paths / package manager (no Raspberry Pi vcgencmd).
log " + Installing sudoers rules for the pifire group"
$SUDO tee /etc/sudoers.d/pifire > /dev/null <<'EOF'
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

log " + Installing scikit-learn==1.7.2"
uv pip install scikit-learn==1.7.2 2>&1 | tee -a "$LOG" || { log " !! scikit-learn install failed. Exiting."; exit 1; }

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

# Drop the PiFire site into conf.d and make it the default server. Fedora ships a
# default server inside nginx.conf, so strip its default_server flag to avoid a
# duplicate-default conflict (PiFire then handles all unmatched requests).
log " + Installing PiFire nginx site (conf.d/pifire.conf)"
$SUDO sed -i 's/[[:space:]]*default_server//g' /etc/nginx/nginx.conf
$SUDO cp /usr/local/bin/pifire/auto-install/nginx/server_error.html /usr/share/nginx/html/server_error.html
$SUDO tee /etc/nginx/conf.d/pifire.conf > /dev/null <<'EOF'
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
    $SUDO firewall-cmd --permanent --add-service=http  2>&1 | tee -a "$LOG"
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
for prog in control webapp; do
    tmp="/tmp/pifire-$prog.ini"
    cp "/usr/local/bin/pifire/auto-install/supervisor/$prog.conf" "$tmp"
    echo "user=$USER" >> "$tmp"
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
    } | $SUDO tee -a /etc/supervisord.conf > /dev/null
fi

log " + Enabling and starting supervisord"
$SUDO systemctl enable supervisord 2>&1 | tee -a "$LOG"
$SUDO systemctl restart supervisord 2>&1 | tee -a "$LOG"

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
