"""Run install.sh against a temporary root and check what it writes."""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]

# The relevant part of Debian 12's /etc/gdm3/daemon.conf.
GDM_DEFAULT = """\
# GDM configuration storage
[daemon]
# Uncoment the line below to force the login screen to use Xorg
#WaylandEnable=false

# Enabling automatic login
#  AutomaticLoginEnable = true
#  AutomaticLogin = user1

[security]

[xdmcp]
"""

BASE = {
    "KIOSK_URL": "http://10.0.0.5:8080/panel/",
    "KIOSK_USER": "kiosk",
    "DISPLAY_IDLE": "300",
    "TOUCH_USB_ID": "14e1:6000",
    "TOUCH_CALIBRATION": "1.2492 0.0 -0.1207 0.0 1.1355 -0.0665",
}


def install(tmp_path, gnome="43", extra_args=(), **overrides):
    values = {**BASE, **overrides}
    conf = tmp_path / "kiosk.conf"
    conf.write_text("".join(f'{k}="{v}"\n' for k, v in values.items()))
    root = tmp_path / "root"
    gdm = root / "etc/gdm3/daemon.conf"
    if not gdm.exists():
        gdm.parent.mkdir(parents=True)
        gdm.write_text(GDM_DEFAULT)
    return subprocess.run(
        ["bash", str(REPO / "install.sh"), "--root", str(root), "--config", str(conf), *extra_args],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "KIOSK_GNOME_VERSION": gnome},
    ), root


def test_writes_the_kiosk(tmp_path):
    result, root = install(tmp_path)
    assert result.returncode == 0, result.stderr
    for path in [
        "etc/kiosk-kit/kiosk.conf", "usr/local/bin/kiosk-browser", "usr/local/sbin/kiosk-display",
        "etc/xdg/autostart/kiosk-browser.desktop", "etc/chromium/policies/managed/kiosk-kit.json",
        "etc/dconf/db/local.d/00-kiosk-kit", "etc/dconf/db/local.d/locks/kiosk-kit",
        "etc/systemd/system/kiosk-display.service", "etc/apt/apt.conf.d/52kiosk-kit-unattended-upgrades",
        "usr/share/gnome-shell/extensions/kiosk-no-overview@kiosk-kit/metadata.json",
    ]:
        assert (root / path).is_file(), path
    assert (root / "usr/local/bin/kiosk-browser").stat().st_mode & 0o111
    # Commands are only announced, never run, with --root.
    assert "would run: dconf update" in result.stdout


def test_runtime_config_is_complete_with_defaults(tmp_path):
    _, root = install(tmp_path)
    conf = (root / "etc/kiosk-kit/kiosk.conf").read_text()
    assert 'KIOSK_URL="http://10.0.0.5:8080/panel/"' in conf
    assert 'WAIT_TIMEOUT="120"' in conf and 'REBOOT_TIME="04:00"' in conf


def test_autologin_replaces_the_commented_lines(tmp_path):
    install(tmp_path)
    result, root = install(tmp_path)  # second run must not duplicate anything
    assert result.returncode == 0
    gdm = (root / "etc/gdm3/daemon.conf").read_text()
    assert gdm.count("AutomaticLoginEnable") == 1
    assert "AutomaticLoginEnable=true\nAutomaticLogin=kiosk\n" in gdm
    assert gdm.index("AutomaticLogin=") < gdm.index("[security]")


def test_touch_calibration_rule(tmp_path):
    _, root = install(tmp_path)
    rule = (root / "etc/udev/rules.d/99-kiosk-touch-calibration.rules").read_text()
    assert 'ATTRS{idVendor}=="14e1", ATTRS{idProduct}=="6000"' in rule
    assert 'LIBINPUT_CALIBRATION_MATRIX}="1.2492 0.0 -0.1207 0.0 1.1355 -0.0665"' in rule


def test_extension_format_follows_gnome_version(tmp_path):
    _, root = install(tmp_path, gnome="43")
    ext = root / "usr/share/gnome-shell/extensions/kiosk-no-overview@kiosk-kit"
    assert "imports.ui.main" in (ext / "extension.js").read_text()
    assert '"43"' in (ext / "metadata.json").read_text()
    _, root = install(tmp_path, gnome="46")
    assert "export default class" in (ext / "extension.js").read_text()
    assert '"46"' in (ext / "metadata.json").read_text()


def test_idle_zero_skips_the_display_service(tmp_path):
    result, root = install(tmp_path, DISPLAY_IDLE="0")
    assert result.returncode == 0
    assert not (root / "etc/systemd/system/kiosk-display.service").exists()


def test_dry_run_writes_nothing(tmp_path):
    result, root = install(tmp_path, extra_args=["--dry-run"])
    assert result.returncode == 0
    assert "would write /etc/kiosk-kit/kiosk.conf" in result.stdout
    assert not (root / "etc/kiosk-kit").exists()


@pytest.mark.parametrize("key,value,message", [
    ("KIOSK_URL", "ftp://x", "KIOSK_URL"),
    ("KIOSK_USER", "Root User", "KIOSK_USER"),
    ("DISPLAY_IDLE", "5min", "DISPLAY_IDLE"),
    ("TOUCH_CALIBRATION", "1 0 0", "six numbers"),
    ("TOUCH_USB_ID", "penmount", "vendor:product"),
    ("REBOOT_TIME", "25:00", "HH:MM"),
])
def test_invalid_config_is_rejected(tmp_path, key, value, message):
    result, root = install(tmp_path, **{key: value})
    assert result.returncode == 2
    assert message in result.stderr
    assert not (root / "etc/kiosk-kit").exists()
