#!/usr/bin/env bash
# Install presence-display as a systemd user service on Raspberry Pi OS (labwc).
# Run as the desktop/kiosk user (not root):  ./install.sh
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run this as the kiosk user, not root (it installs a user service)." >&2
  exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing packages (sudo)"
sudo apt-get update -qq
sudo apt-get install -y python3-gpiozero python3-lgpio python3-paho-mqtt wlopm

if ! id -nG "$USER" | grep -qw gpio; then
  echo "==> Adding $USER to the gpio group (log out/in or reboot afterwards)"
  sudo usermod -aG gpio "$USER"
fi

echo "==> Installing script and service"
install -Dm755 "$here/presence_display.py" "$HOME/.local/bin/presence_display.py"
install -Dm644 "$here/presence-display.service" "$HOME/.config/systemd/user/presence-display.service"
cfg="$HOME/.config/presence-display/presence-display.env"
if [[ ! -f "$cfg" ]]; then
  install -Dm600 "$here/presence-display.env.example" "$cfg"
  echo "    wrote default config to $cfg"
else
  echo "    keeping existing config at $cfg"
fi

systemctl --user daemon-reload
systemctl --user enable --now presence-display.service

echo
echo "Done. Useful commands:"
echo "  journalctl --user -u presence-display -f     # watch it work"
echo "  nano $cfg                                     # MQTT, delay, GPIO"
echo "  systemctl --user restart presence-display"
