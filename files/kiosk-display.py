#!/usr/bin/python3
"""Switch the display off after DISPLAY_IDLE seconds without input; a touch wakes it.

While the display is dark, the touchscreen is grabbed exclusively (EVIOCGRAB), so
the waking touch never reaches the browser. The grab is released once the finger
has been lifted.

Why not GNOME's own screen blanking: Mutter disables the touchscreen while the
output is in power-save mode, so no touch would ever wake it. The evdev events
still arrive at the device, which is what this service listens to.
"""

from __future__ import annotations

import fcntl
import glob
import os
import pwd
import select
import signal
import struct
import subprocess
import sys
import time

EVIOCGRAB = 0x40044590
EVENT = struct.Struct("llHHi")
EV_KEY, BTN_TOUCH = 0x01, 0x14A
RESCAN_S = 30  # look for new input devices this often


class IdleController:
    """Pure state machine: feed it input and ticks, it returns actions to perform.

    States: ``awake`` -> (idle) -> ``dark`` -> (input) -> ``waking`` -> (finger
    lifted) -> ``awake``. Actions: ``grab``, ``release``, ``display_off``,
    ``display_on``.
    """

    def __init__(self, idle_s: float, release_quiet_s: float = 0.4):
        self.idle_s = idle_s
        self.release_quiet_s = release_quiet_s
        self.state = "awake"
        self.last_input = 0.0
        self.last_touch = 0.0
        self.finger_down = False

    def start(self, now: float) -> None:
        self.last_input = now

    def on_input(self, now: float, touch: bool = False, finger: bool | None = None) -> list[str]:
        self.last_input = now
        if touch:
            self.last_touch = now
            if finger is not None:
                self.finger_down = finger
        if self.state == "dark":
            self.state = "waking"
            return ["display_on"]
        return []

    def tick(self, now: float) -> list[str]:
        if (self.state == "waking" and not self.finger_down
                and now - self.last_touch >= self.release_quiet_s):
            self.state = "awake"
            return ["release"]
        if self.state == "awake" and self.idle_s > 0 and now - self.last_input >= self.idle_s:
            return ["grab", "display_off"]
        return []

    def display_off_result(self, ok: bool, now: float) -> list[str]:
        """Report whether switching off worked (it fails before the session is up)."""
        if ok:
            self.state = "dark"
            return []
        self.last_input = now
        return ["release"]

    def timeout(self, now: float) -> float:
        if self.state == "waking":
            return self.release_quiet_s
        if self.state == "dark" or self.idle_s <= 0:
            return RESCAN_S
        return max(0.5, min(RESCAN_S, self.last_input + self.idle_s - now))


class MutterDisplay:
    """DPMS through Mutter: org.gnome.Mutter.DisplayConfig.PowerSaveMode (0 on, 3 off)."""

    def __init__(self, user: str):
        self.user = user
        self.bus = f"unix:path=/run/user/{pwd.getpwnam(user).pw_uid}/bus"

    def _set(self, mode: int) -> bool:
        r = subprocess.run(
            ["runuser", "-u", self.user, "--", "env", f"DBUS_SESSION_BUS_ADDRESS={self.bus}",
             "gdbus", "call", "--session", "--dest", "org.gnome.Mutter.DisplayConfig",
             "--object-path", "/org/gnome/Mutter/DisplayConfig",
             "--method", "org.freedesktop.DBus.Properties.Set",
             "org.gnome.Mutter.DisplayConfig", "PowerSaveMode", f"<{mode}>"],
            capture_output=True, text=True, timeout=10)
        if r.returncode:
            log(f"PowerSaveMode {mode}: {r.stderr.strip()}")
        return r.returncode == 0

    def off(self) -> bool:
        return self._set(3)

    def on(self) -> bool:
        return self._set(0)


def log(msg: str) -> None:
    print(msg, flush=True)


def device_name(path: str) -> str:
    try:
        with open(f"/sys/class/input/{os.path.basename(path)}/device/name") as f:
            return f.read().strip()
    except OSError:
        return ""


def is_touchscreen(path: str, name_filter: str) -> bool:
    if name_filter:
        return name_filter in device_name(path)
    # No filter: trust udev's classification of the event device.
    try:
        rdev = os.stat(path).st_rdev
        with open(f"/run/udev/data/c{os.major(rdev)}:{os.minor(rdev)}") as f:
            return "E:ID_INPUT_TOUCHSCREEN=1" in f.read().split("\n")
    except OSError:
        return False


class DisplayService:
    def __init__(self, controller: IdleController, display: MutterDisplay, touch_name: str):
        self.ctl = controller
        self.display = display
        self.touch_name = touch_name
        self.devices: dict[int, tuple[str, bool]] = {}  # fd -> (path, is touch)

    def scan(self) -> None:
        known = {path for path, _ in self.devices.values()}
        for path in glob.glob("/dev/input/event*"):
            if path in known:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            touch = is_touchscreen(path, self.touch_name)
            self.devices[fd] = (path, touch)
            if touch:
                log(f"touchscreen: {path} ({device_name(path)})")
                if self.ctl.state != "awake":
                    self._grab(fd, True)

    def _grab(self, fd: int, on: bool) -> None:
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)
        except OSError as e:
            log(f"EVIOCGRAB {on} {self.devices[fd][0]}: {e}")

    def perform(self, actions: list[str]) -> None:
        for action in actions:
            if action in ("grab", "release"):
                for fd, (_, touch) in self.devices.items():
                    if touch:
                        self._grab(fd, action == "grab")
            elif action == "display_off":
                ok = self.display.off()
                log("display off" if ok else "display off failed, retrying later")
                self.perform(self.ctl.display_off_result(ok, time.monotonic()))
            elif action == "display_on":
                self.display.on()
                log("display on")

    def read(self, fd: int) -> None:
        path, touch = self.devices[fd]
        try:
            data = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            del self.devices[fd]
            os.close(fd)
            log(f"device gone: {path}")
            return
        finger = None
        if touch:
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, typ, code, value = EVENT.unpack_from(data, i)
                if typ == EV_KEY and code == BTN_TOUCH:
                    finger = value != 0
        self.perform(self.ctl.on_input(time.monotonic(), touch, finger))

    def run(self) -> None:
        self.ctl.start(time.monotonic())
        self.scan()
        next_scan = time.monotonic() + RESCAN_S
        while True:
            ready, _, _ = select.select(list(self.devices), [], [],
                                        self.ctl.timeout(time.monotonic()))
            for fd in ready:
                if fd in self.devices:
                    self.read(fd)
            now = time.monotonic()
            self.perform(self.ctl.tick(now))
            if now >= next_scan:
                self.scan()
                next_scan = now + RESCAN_S

    def shutdown(self, *_: object) -> None:
        self.perform(["release", "display_on"])
        sys.exit(0)


def main() -> None:
    idle = int(os.environ.get("DISPLAY_IDLE", "300"))
    user = os.environ.get("KIOSK_USER", "kiosk")
    service = DisplayService(IdleController(idle), MutterDisplay(user),
                             os.environ.get("TOUCH_NAME", ""))
    signal.signal(signal.SIGTERM, service.shutdown)
    signal.signal(signal.SIGINT, service.shutdown)
    log(f"display off after {idle} s without input (session of {user})")
    service.run()


if __name__ == "__main__":
    main()
