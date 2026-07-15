# This file will add the kernel support for 1-wire on (GPIO 4)
# Skips the raspi-config call (and reports no reboot needed) if the w1-gpio overlay is
# already active in config.txt, so re-running the wizard with the same selection
# doesn't force a reboot for something that hasn't changed.

CONFIG="${PIFIRE_CONFIG_TXT:-}"
if [ -z "$CONFIG" ]; then
	if [ -f /boot/firmware/config.txt ]; then
		CONFIG='/boot/firmware/config.txt'
	else
		CONFIG='/boot/config.txt'
	fi
fi

if grep -Eq '^dtoverlay=w1-gpio(,|[[:space:]]|$)' "$CONFIG" 2>/dev/null; then
	echo "1-Wire (GPIO4) already enabled in $CONFIG"
	echo "REBOOT_REQUIRED=false"
else
	sudo raspi-config nonint do_onewire 0   # Enable 1-wire support
	echo "REBOOT_REQUIRED=true"
fi
