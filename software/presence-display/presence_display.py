#!/usr/bin/env python3
"""
presence-display: wake / sleep a Wayland kiosk display from an LD2410C mmWave sensor.

The LD2410C OUT pin (3.3 V, high = someone present) is wired to a Raspberry Pi GPIO.
When presence goes high the display is turned on immediately. When it goes low the
display is turned off after OFF_DELAY seconds, unless presence returns first.

Optionally publishes to MQTT with Home Assistant discovery:
  - binary_sensor  "Presence"      (occupancy)
  - binary_sensor  "Display"       (power)
  - switch         "Auto screen"   (off = script stops touching the display and leaves it on)

All settings come from environment variables (see presence-display.env.example) and can be
overridden with command-line flags.
"""
import argparse
import glob
import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import threading

log = logging.getLogger("presence-display")


def env_bool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def ensure_wayland_env():
    """systemd user services don't always inherit WAYLAND_DISPLAY; find the socket ourselves."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    os.environ.setdefault("XDG_RUNTIME_DIR", runtime)
    if os.environ.get("WAYLAND_DISPLAY"):
        return os.environ["WAYLAND_DISPLAY"]
    socks = sorted(p for p in glob.glob(os.path.join(runtime, "wayland-*")) if not p.endswith(".lock"))
    if socks:
        os.environ["WAYLAND_DISPLAY"] = os.path.basename(socks[0])
    return os.environ.get("WAYLAND_DISPLAY")


class Display:
    """Runs the on/off commands and remembers what it last did."""

    def __init__(self, on_cmd, off_cmd, dry_run=False):
        self.on_cmd = shlex.split(on_cmd)
        self.off_cmd = shlex.split(off_cmd)
        self.dry_run = dry_run
        self.is_on = None  # unknown until first command

    def set(self, on, force=False):
        if not force and self.is_on == on:
            return True
        cmd = self.on_cmd if on else self.off_cmd
        log.info("display %s: %s", "ON" if on else "OFF", " ".join(cmd))
        if self.dry_run:
            self.is_on = on
            return True
        try:
            subprocess.run(cmd, check=True, timeout=10, capture_output=True, text=True)
        except FileNotFoundError:
            log.error("command not found: %s", cmd[0])
            return False
        except subprocess.CalledProcessError as e:
            log.error("display command failed (%s): %s", e.returncode, (e.stderr or "").strip())
            return False
        except subprocess.TimeoutExpired:
            log.error("display command timed out: %s", " ".join(cmd))
            return False
        self.is_on = on
        return True


class Controller:
    """Presence -> display state machine with an off delay and an 'auto' override."""

    def __init__(self, display, off_delay, on_change=None):
        self.display = display
        self.off_delay = float(off_delay)
        self.on_change = on_change or (lambda **kw: None)
        self.present = False
        self.auto = True
        self._timer = None
        self._lock = threading.RLock()

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _turn_off_now(self):
        with self._lock:
            self._timer = None
            if self.auto and not self.present:
                self.display.set(False)
                self.on_change(display=self.display.is_on)

    def presence(self, present):
        with self._lock:
            self.present = bool(present)
            log.info("presence %s", "detected" if self.present else "cleared")
            self.on_change(presence=self.present)
            if not self.auto:
                return
            if self.present:
                self._cancel_timer()
                self.display.set(True)
                self.on_change(display=self.display.is_on)
            else:
                self._cancel_timer()
                if self.off_delay <= 0:
                    self._turn_off_now()
                else:
                    self._timer = threading.Timer(self.off_delay, self._turn_off_now)
                    self._timer.daemon = True
                    self._timer.start()

    def set_auto(self, enabled):
        with self._lock:
            self.auto = bool(enabled)
            log.info("auto screen %s", "enabled" if self.auto else "disabled")
            self.on_change(auto=self.auto)
            if not self.auto:
                # manual mode: make sure the screen is on and leave it alone
                self._cancel_timer()
                self.display.set(True)
                self.on_change(display=self.display.is_on)
            else:
                self.presence(self.present)

    def shutdown(self, leave_on=True):
        with self._lock:
            self._cancel_timer()
            if leave_on:
                self.display.set(True)


class MqttBridge:
    """Optional MQTT publishing with Home Assistant discovery."""

    def __init__(self, args, controller_ref):
        import paho.mqtt.client as mqtt  # imported lazily so MQTT stays optional

        self.mqtt = mqtt
        self.ctl = controller_ref
        host = socket.gethostname()
        self.node = args.mqtt_node_id or f"presence_display_{host}".replace("-", "_")
        self.base = f"presence-display/{self.node}"
        self.prefix = args.mqtt_discovery_prefix
        self.name = args.mqtt_device_name or f"{host} calendar display"
        try:  # paho-mqtt >= 2.0
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.node)
        except AttributeError:  # paho-mqtt 1.x (Debian bookworm)
            self.client = mqtt.Client(client_id=self.node)
        if args.mqtt_user:
            self.client.username_pw_set(args.mqtt_user, args.mqtt_password or None)
        self.client.will_set(f"{self.base}/availability", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 60)
        self.host, self.port = args.mqtt_host, args.mqtt_port

    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        self.client.publish(f"{self.base}/availability", "offline", retain=True)
        self.client.loop_stop()
        self.client.disconnect()

    def _device(self):
        return {"identifiers": [self.node], "name": self.name,
                "manufacturer": "DIY", "model": "Raspberry Pi + LD2410C"}

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        rc_val = getattr(rc, "value", rc)
        if rc_val != 0:
            log.error("MQTT connect failed: %s", rc)
            return
        log.info("MQTT connected to %s:%s", self.host, self.port)
        avail = {"availability_topic": f"{self.base}/availability", "device": self._device()}
        configs = {
            ("binary_sensor", "presence"): {
                "name": "Presence", "device_class": "occupancy",
                "state_topic": f"{self.base}/presence"},
            ("binary_sensor", "display"): {
                "name": "Display", "device_class": "power",
                "state_topic": f"{self.base}/display"},
            ("switch", "auto"): {
                "name": "Auto screen", "icon": "mdi:monitor-eye",
                "state_topic": f"{self.base}/auto",
                "command_topic": f"{self.base}/auto/set"},
        }
        for (component, obj), cfg in configs.items():
            cfg.update(avail)
            cfg["unique_id"] = f"{self.node}_{obj}"
            client.publish(f"{self.prefix}/{component}/{self.node}/{obj}/config",
                           json.dumps(cfg), retain=True)
        client.subscribe(f"{self.base}/auto/set")
        client.publish(f"{self.base}/availability", "online", retain=True)
        ctl = self.ctl()
        self.publish(presence=ctl.present, display=ctl.display.is_on, auto=ctl.auto)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="ignore").strip().upper()
        if msg.topic.endswith("/auto/set") and payload in ("ON", "OFF"):
            self.ctl().set_auto(payload == "ON")

    def publish(self, **state):
        for key, val in state.items():
            if val is None:
                continue
            self.client.publish(f"{self.base}/{key}", "ON" if val else "OFF", retain=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    e = os.environ.get
    p.add_argument("--gpio", type=int, default=int(e("PRESENCE_GPIO", "17")), help="BCM pin for LD2410C OUT")
    p.add_argument("--off-delay", type=float, default=float(e("OFF_DELAY", "30")),
                   help="seconds without presence before the display turns off")
    p.add_argument("--on-cmd", default=e("DISPLAY_ON_CMD", "wlopm --on *"))
    p.add_argument("--off-cmd", default=e("DISPLAY_OFF_CMD", "wlopm --off *"))
    p.add_argument("--dry-run", action="store_true", default=env_bool("DRY_RUN", False),
                   help="log display changes without running the commands")
    p.add_argument("--mqtt-host", default=e("MQTT_HOST", ""), help="enable MQTT by setting a broker host")
    p.add_argument("--mqtt-port", type=int, default=int(e("MQTT_PORT", "1883")))
    p.add_argument("--mqtt-user", default=e("MQTT_USER", ""))
    p.add_argument("--mqtt-password", default=e("MQTT_PASSWORD", ""))
    p.add_argument("--mqtt-discovery-prefix", default=e("MQTT_DISCOVERY_PREFIX", "homeassistant"))
    p.add_argument("--mqtt-node-id", default=e("MQTT_NODE_ID", ""))
    p.add_argument("--mqtt-device-name", default=e("MQTT_DEVICE_NAME", ""))
    p.add_argument("--log-level", default=e("LOG_LEVEL", "INFO"))
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    wl = ensure_wayland_env()
    log.info("WAYLAND_DISPLAY=%s XDG_RUNTIME_DIR=%s", wl, os.environ.get("XDG_RUNTIME_DIR"))

    from gpiozero import DigitalInputDevice

    bridge = None
    ctl_holder = {}

    def on_change(**state):
        if bridge is not None:
            bridge.publish(**state)

    display = Display(args.on_cmd, args.off_cmd, dry_run=args.dry_run)
    ctl = Controller(display, args.off_delay, on_change=on_change)
    ctl_holder["c"] = ctl

    if args.mqtt_host:
        bridge = MqttBridge(args, lambda: ctl_holder["c"])
        bridge.start()

    # LD2410C OUT is push-pull; the pull-down keeps the input low if the wire comes loose.
    sensor = DigitalInputDevice(args.gpio, pull_up=False)
    sensor.when_activated = lambda: ctl.presence(True)
    sensor.when_deactivated = lambda: ctl.presence(False)

    # Start with the screen on, then let the current sensor state decide.
    display.set(True, force=True)
    ctl.presence(sensor.is_active)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("watching GPIO%d, off delay %.0fs%s", args.gpio, args.off_delay,
             f", MQTT {args.mqtt_host}" if args.mqtt_host else "")
    stop.wait()

    log.info("shutting down, leaving display on")
    ctl.shutdown(leave_on=True)
    sensor.close()
    if bridge is not None:
        bridge.publish(display=display.is_on)
        bridge.stop()


if __name__ == "__main__":
    main()
