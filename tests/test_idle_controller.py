import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "kiosk_display", Path(__file__).parents[1] / "files" / "kiosk-display.py")
kiosk_display = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kiosk_display)
IdleController = kiosk_display.IdleController


@pytest.fixture
def ctl():
    c = IdleController(idle_s=300, release_quiet_s=0.4)
    c.start(0.0)
    return c


def go_dark(ctl, now=300.0):
    assert ctl.tick(now) == ["grab", "display_off"]
    assert ctl.display_off_result(True, now) == []
    assert ctl.state == "dark"


def test_stays_on_while_used(ctl):
    assert ctl.tick(299.0) == []
    ctl.on_input(250.0)
    assert ctl.tick(400.0) == []  # 150 s since the last input


def test_goes_dark_after_idle_with_touch_grabbed_first(ctl):
    go_dark(ctl)


def test_touch_wakes_and_is_swallowed_until_finger_lifts(ctl):
    go_dark(ctl)
    assert ctl.on_input(310.0, touch=True, finger=True) == ["display_on"]
    assert ctl.state == "waking"
    # Finger still on the glass: keep the grab, however long it stays.
    assert ctl.tick(320.0) == []
    ctl.on_input(320.1, touch=True, finger=False)
    assert ctl.tick(320.2) == []           # lifted, but not quiet long enough yet
    assert ctl.tick(320.6) == ["release"]
    assert ctl.state == "awake"


def test_further_touches_while_waking_do_not_switch_on_again(ctl):
    go_dark(ctl)
    ctl.on_input(310.0, touch=True, finger=True)
    assert ctl.on_input(310.1, touch=True) == []


def test_keyboard_wakes_and_releases_without_a_finger(ctl):
    go_dark(ctl)
    assert ctl.on_input(310.0) == ["display_on"]
    assert ctl.tick(310.5) == ["release"]


def test_failed_switch_off_releases_and_restarts_the_timer(ctl):
    assert ctl.tick(300.0) == ["grab", "display_off"]
    assert ctl.display_off_result(False, 300.0) == ["release"]
    assert ctl.state == "awake"
    assert ctl.tick(400.0) == []
    assert ctl.tick(600.0) == ["grab", "display_off"]


def test_idle_zero_never_goes_dark():
    c = IdleController(idle_s=0)
    c.start(0.0)
    assert c.tick(10_000.0) == []
    assert c.timeout(10_000.0) == kiosk_display.RESCAN_S


def test_timeout_tracks_the_next_deadline(ctl):
    assert ctl.timeout(0.0) == kiosk_display.RESCAN_S   # capped
    assert ctl.timeout(290.0) == pytest.approx(10.0)
    assert ctl.timeout(299.9) == 0.5                    # never busy-loops
    go_dark(ctl)
    ctl.on_input(310.0, touch=True, finger=True)
    assert ctl.timeout(310.0) == 0.4
