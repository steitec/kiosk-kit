#!/bin/bash
# kiosk-kit installer: turns a Debian machine with GNOME into a browser kiosk.
#
#   sudo ./install.sh [--config kiosk.conf] [--dry-run]
#   ./install.sh --root DIR [--config kiosk.conf]     # write files below DIR only
#
# Safe to run again after changing the config: every file is rewritten from
# scratch and settings are replaced, not appended.
set -euo pipefail

src="$(cd "$(dirname "$0")" && pwd)"
config="$src/kiosk.conf"
root=""
dry=0

usage() { sed -n '4,5p' "$0" | sed 's/^# \{0,3\}//'; exit 2; }
die() { echo "error: $*" >&2; exit 2; }

while (($#)); do
  case "$1" in
    --config) config="${2:?}"; shift 2 ;;
    --root) root="${2:?}"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    -h | --help) usage ;;
    *) die "unknown option $1" ;;
  esac
done

[[ -f "$config" ]] || die "config $config not found"
[[ -n "$root" || $dry -eq 1 || $EUID -eq 0 ]] || die "run with sudo (or use --dry-run / --root DIR)"

# -- config -----------------------------------------------------------------

# shellcheck source=kiosk.conf
. "$config"
: "${KIOSK_URL:?} ${KIOSK_USER:?}"
KIOSK_LANG="${KIOSK_LANG:-en}" WAIT_TIMEOUT="${WAIT_TIMEOUT:-120}" CLEAR_CACHE="${CLEAR_CACHE:-yes}"
DISPLAY_IDLE="${DISPLAY_IDLE:-300}" TOUCH_NAME="${TOUCH_NAME:-}" TOUCH_USB_ID="${TOUCH_USB_ID:-}"
TOUCH_CALIBRATION="${TOUCH_CALIBRATION:-}" UPDATES="${UPDATES:-yes}" REBOOT_TIME="${REBOOT_TIME:-04:00}"

num='-?[0-9]+(\.[0-9]+)?'
[[ "$KIOSK_URL" =~ ^https?://[^[:space:]\"]+$ ]] || die "KIOSK_URL must start with http:// or https://"
[[ "$KIOSK_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "KIOSK_USER is not a valid user name"
[[ "$KIOSK_LANG" =~ ^[A-Za-z_-]+$ ]] || die "KIOSK_LANG must look like en or de_DE"
[[ "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]] || die "WAIT_TIMEOUT must be a whole number of seconds"
[[ "$DISPLAY_IDLE" =~ ^[0-9]+$ ]] || die "DISPLAY_IDLE must be a whole number of seconds"
[[ "$CLEAR_CACHE" =~ ^(yes|no)$ ]] || die "CLEAR_CACHE must be yes or no"
[[ "$UPDATES" =~ ^(yes|no)$ ]] || die "UPDATES must be yes or no"
[[ "$REBOOT_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die "REBOOT_TIME must be HH:MM"
[[ "$TOUCH_NAME" != *\"* ]] || die "TOUCH_NAME must not contain quotes"
if [[ -n "$TOUCH_USB_ID$TOUCH_CALIBRATION" ]]; then
  [[ "$TOUCH_USB_ID" =~ ^[0-9a-f]{4}:[0-9a-f]{4}$ ]] || die "TOUCH_USB_ID must be vendor:product in hex, e.g. 14e1:6000"
  [[ "$TOUCH_CALIBRATION" =~ ^$num([[:space:]]+$num){5}$ ]] || die "TOUCH_CALIBRATION needs six numbers"
fi

# -- helpers ----------------------------------------------------------------

# put PATH MODE: write stdin to PATH (below --root), atomically.
put() {
  local dest="$root$1"
  if ((dry)); then echo "would write $1"; cat >/dev/null; return; fi
  mkdir -p "$(dirname "$dest")"
  cat >"$dest.tmp"
  chmod "$2" "$dest.tmp"
  mv "$dest.tmp" "$dest"
  echo "wrote $1"
}

# run CMD...: execute on the real system only.
run() {
  if ((dry)) || [[ -n "$root" ]]; then echo "would run: $*"; else "$@"; fi
}

# ini_set FILE SECTION KEY VALUE: set KEY=VALUE in [SECTION], replacing an
# existing (or commented-out) KEY line instead of adding a second one.
ini_set() {
  local file="$root$1"
  if ((dry)); then echo "would set [$2] $3=$4 in $1"; return; fi
  mkdir -p "$(dirname "$file")"
  [[ -f "$file" ]] || : >"$file"
  awk -v sec="[$2]" -v key="$3" -v val="$4" '
    function emit() { if (!done) { print key "=" val; done = 1 } }
    $0 == sec { print; insec = 1; next }
    /^\[/ { if (insec) emit(); insec = 0; print; next }
    insec && $0 ~ ("^[#[:space:]]*" key "[[:space:]]*=") { emit(); next }
    { print }
    END { if (!done) { if (!insec) print sec; print key "=" val } }
  ' "$file" >"$file.tmp"
  mv "$file.tmp" "$file"
  echo "set [$2] $3=$4 in $1"
}

gnome_major() {
  if [[ -n "${KIOSK_GNOME_VERSION:-}" ]]; then echo "$KIOSK_GNOME_VERSION"; return; fi
  if command -v gnome-shell >/dev/null; then
    gnome-shell --version | grep -oE '[0-9]+' | head -1
  else
    echo 43
  fi
}

# -- packages and user --------------------------------------------------------

packages=(chromium python3 dconf-cli)
[[ "$UPDATES" == yes ]] && packages+=(unattended-upgrades)
run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${packages[@]}"

if [[ -z "$root" ]] && ! id -u "$KIOSK_USER" >/dev/null 2>&1; then
  run useradd --create-home --shell /bin/bash "$KIOSK_USER"
fi

# -- configuration and programs ----------------------------------------------

# Write back the validated values, complete with defaults: the launcher and the
# display service read this file, so it must not depend on what the source had.
put /etc/kiosk-kit/kiosk.conf 644 <<EOF
# Written by kiosk-kit install.sh -- edit the source config and run it again.
KIOSK_URL="$KIOSK_URL"
KIOSK_USER="$KIOSK_USER"
KIOSK_LANG="$KIOSK_LANG"
WAIT_TIMEOUT="$WAIT_TIMEOUT"
CLEAR_CACHE="$CLEAR_CACHE"
DISPLAY_IDLE="$DISPLAY_IDLE"
TOUCH_NAME="$TOUCH_NAME"
TOUCH_USB_ID="$TOUCH_USB_ID"
TOUCH_CALIBRATION="$TOUCH_CALIBRATION"
UPDATES="$UPDATES"
REBOOT_TIME="$REBOOT_TIME"
EOF
put /usr/local/bin/kiosk-browser 755 <"$src/files/kiosk-browser"
put /usr/local/sbin/kiosk-display 755 <"$src/files/kiosk-display.py"

put /etc/xdg/autostart/kiosk-browser.desktop 644 <<'EOF'
[Desktop Entry]
Type=Application
Name=Kiosk browser
Exec=/usr/local/bin/kiosk-browser
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

put /etc/chromium/policies/managed/kiosk-kit.json 644 <<'EOF'
{
  "TranslateEnabled": false,
  "PasswordManagerEnabled": false,
  "AutofillAddressEnabled": false,
  "AutofillCreditCardEnabled": false,
  "BrowserSignin": 0,
  "DefaultNotificationsSetting": 2,
  "DefaultGeolocationSetting": 2
}
EOF

# -- GNOME session: autologin, no lock, no blanking, no overview --------------

ini_set /etc/gdm3/daemon.conf daemon AutomaticLoginEnable true
ini_set /etc/gdm3/daemon.conf daemon AutomaticLogin "$KIOSK_USER"

ext=kiosk-no-overview@kiosk-kit
if (($(gnome_major) >= 45)); then
  put "/usr/share/gnome-shell/extensions/$ext/extension.js" 644 <"$src/files/extension/extension-esm.js"
  versions='["45", "46", "47", "48"]'
else
  put "/usr/share/gnome-shell/extensions/$ext/extension.js" 644 <"$src/files/extension/extension-legacy.js"
  versions='["40", "41", "42", "43", "44"]'
fi
put "/usr/share/gnome-shell/extensions/$ext/metadata.json" 644 <<EOF
{
  "uuid": "$ext",
  "name": "Kiosk: no overview at startup",
  "description": "Keeps GNOME from opening the Activities overview after login, so the kiosk browser is visible at once.",
  "shell-version": $versions
}
EOF

# System-wide GNOME settings via dconf: they apply without a running session and
# are locked, so nothing in the session can turn the screen lock back on.
put /etc/dconf/profile/user 644 <<'EOF'
user-db:user
system-db:local
EOF
put /etc/dconf/db/local.d/00-kiosk-kit 644 <<EOF
[org/gnome/desktop/session]
idle-delay=uint32 0

[org/gnome/desktop/screensaver]
lock-enabled=false
idle-activation-enabled=false

[org/gnome/desktop/lockdown]
disable-lock-screen=true

[org/gnome/settings-daemon/plugins/power]
sleep-inactive-ac-type='nothing'
sleep-inactive-battery-type='nothing'
idle-dim=false

[org/gnome/desktop/notifications]
show-banners=false

[org/gnome/shell]
disable-user-extensions=false
enabled-extensions=['$ext']
EOF
put /etc/dconf/db/local.d/locks/kiosk-kit 644 <<'EOF'
/org/gnome/desktop/session/idle-delay
/org/gnome/desktop/screensaver/lock-enabled
/org/gnome/desktop/screensaver/idle-activation-enabled
/org/gnome/desktop/lockdown/disable-lock-screen
/org/gnome/settings-daemon/plugins/power/sleep-inactive-ac-type
/org/gnome/settings-daemon/plugins/power/idle-dim
/org/gnome/shell/enabled-extensions
EOF
run dconf update

# -- display power: off when idle, touch wakes ----------------------------------

if ((DISPLAY_IDLE > 0)); then
  put /etc/systemd/system/kiosk-display.service 644 <<'EOF'
[Unit]
Description=Kiosk: display off after inactivity, touch wakes it
After=systemd-udev-settle.service graphical.target

[Service]
EnvironmentFile=/etc/kiosk-kit/kiosk.conf
ExecStart=/usr/local/sbin/kiosk-display
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
  run systemctl daemon-reload
  run systemctl enable kiosk-display.service
  run systemctl restart kiosk-display.service
else
  echo "DISPLAY_IDLE=0: display stays on, kiosk-display.service not installed"
  if [[ -z "$root" && -f /etc/systemd/system/kiosk-display.service ]]; then
    run systemctl disable --now kiosk-display.service
    run rm -f /etc/systemd/system/kiosk-display.service
  fi
fi

# -- touch calibration ----------------------------------------------------------

if [[ -n "$TOUCH_USB_ID" ]]; then
  put /etc/udev/rules.d/99-kiosk-touch-calibration.rules 644 <<EOF
# kiosk-kit: libinput calibration for the touchscreen $TOUCH_USB_ID (Wayland and X11)
ACTION=="add|change", KERNEL=="event[0-9]*", ATTRS{idVendor}=="${TOUCH_USB_ID%%:*}", ATTRS{idProduct}=="${TOUCH_USB_ID##*:}", ENV{LIBINPUT_CALIBRATION_MATRIX}="$TOUCH_CALIBRATION"
EOF
  run udevadm control --reload
  run udevadm trigger --subsystem-match=input --action=change
fi

# -- automatic updates ----------------------------------------------------------

if [[ "$UPDATES" == yes ]]; then
  put /etc/apt/apt.conf.d/20auto-upgrades 644 <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
  put /etc/apt/apt.conf.d/52kiosk-kit-unattended-upgrades 644 <<EOF
// kiosk-kit: reboot only when an update needs it (kernel, libc), at night.
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "$REBOOT_TIME";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-New-Unused-Dependencies "true";
EOF
  put /usr/local/sbin/kiosk-chromium-restart 755 <"$src/files/kiosk-chromium-restart"
  put /etc/apt/apt.conf.d/99kiosk-kit-chromium-restart 644 <<'EOF'
// kiosk-kit: restart the kiosk browser after a Chromium update
DPkg::Post-Invoke { "/usr/local/sbin/kiosk-chromium-restart || true"; };
EOF
fi

echo "done -- reboot to start the kiosk"
