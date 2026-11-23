"""Tests for presence_display. Run: GPIOZERO_PIN_FACTORY=mock python3 -m pytest -q"""
import os
import time

os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")

import presence_display as pd  # noqa: E402


class FakeDisplay:
    def __init__(self):
        self.is_on = None
        self.calls = []

    def set(self, on, force=False):
        if not force and self.is_on == on:
            return True
        self.calls.append(on)
        self.is_on = on
        return True


def test_on_immediately_off_after_delay():
    d = FakeDisplay()
    c = pd.Controller(d, off_delay=0.2)
    c.presence(True)
    assert d.is_on is True
    c.presence(False)
    assert d.is_on is True          # still on during the delay
    time.sleep(0.35)
    assert d.is_on is False


def test_presence_returning_cancels_off():
    d = FakeDisplay()
    c = pd.Controller(d, off_delay=0.2)
    c.presence(True)
    c.presence(False)
    time.sleep(0.1)
    c.presence(True)
    time.sleep(0.3)
    assert d.is_on is True
    assert d.calls == [True]        # never turned off, no duplicate commands


def test_zero_delay_turns_off_immediately():
    d = FakeDisplay()
    c = pd.Controller(d, off_delay=0)
    c.presence(True)
    c.presence(False)
    assert d.is_on is False


def test_auto_off_forces_display_on_and_ignores_sensor():
    d = FakeDisplay()
    c = pd.Controller(d, off_delay=0)
    c.presence(False)
    assert d.is_on is False
    c.set_auto(False)
    assert d.is_on is True
    c.presence(False)
    assert d.is_on is True
    c.set_auto(True)                 # re-enabling applies current (absent) state
    assert d.is_on is False


def test_display_runs_real_commands(tmp_path):
    marker = tmp_path / "state"
    d = pd.Display(f"sh -c 'echo on > {marker}'", f"sh -c 'echo off > {marker}'")
    assert d.set(True)
    assert marker.read_text().strip() == "on"
    assert d.set(False)
    assert marker.read_text().strip() == "off"


def test_display_missing_command_is_reported_not_raised():
    d = pd.Display("definitely-not-a-command --on", "definitely-not-a-command --off")
    assert d.set(True) is False
    assert d.is_on is None


def test_gpio_end_to_end_with_mock_pin():
    from gpiozero import Device, DigitalInputDevice
    from gpiozero.pins.mock import MockFactory
    Device.pin_factory = MockFactory()
    d = FakeDisplay()
    c = pd.Controller(d, off_delay=0.1)
    s = DigitalInputDevice(17, pull_up=False)
    s.when_activated = lambda: c.presence(True)
    s.when_deactivated = lambda: c.presence(False)
    pin = Device.pin_factory.pin(17)
    pin.drive_high()
    time.sleep(0.05)
    assert d.is_on is True
    pin.drive_low()
    time.sleep(0.25)
    assert d.is_on is False
    s.close()


def test_wayland_autodetect(tmp_path, monkeypatch):
    (tmp_path / "wayland-0").touch()
    (tmp_path / "wayland-0.lock").touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert pd.ensure_wayland_env() == "wayland-0"
