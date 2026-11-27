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

echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff" \
  | sudo tee /etc/sudoers.d/racedisplay >/dev/null
sudo chmod 440 /etc/sudoers.d/racedisplay
sudo visudo -cf /etc/sudoers.d/racedisplay

echo 'SUBSYSTEM=="backlight", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power", RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power"' \
  | sudo tee /etc/udev/rules.d/99-racedisplay-backlight.rules >/dev/null
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=backlight --action=add

sed "s|__APP_DIR__|$APP_DIR|g; s|__USER__|$USER_NAME|g" "$APP_DIR/racedisplay.service" \
  | sudo tee /etc/systemd/system/racedisplay.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable racedisplay
sudo systemctl restart racedisplay
echo "Installed. Logs: journalctl -u racedisplay -f"
