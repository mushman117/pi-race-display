#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

sudo apt-get update
sudo apt-get install -y python3-pygame python3-requests python3-websocket fonts-dejavu-core python3-evdev \
  libegl1 libgles2 libgbm1 libgl1-mesa-dri

for g in video render input; do
  getent group "$g" >/dev/null && sudo usermod -aG "$g" "$USER_NAME"
done

sed "s|__APP_DIR__|$APP_DIR|g; s|__USER__|$USER_NAME|g" "$APP_DIR/racedisplay.service" \
  | sudo tee /etc/systemd/system/racedisplay.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable racedisplay
sudo systemctl restart racedisplay
echo "Installed. Logs: journalctl -u racedisplay -f"
