#!/bin/sh
printf '\033[9;0]\033[?25l' > /dev/tty1
echo 0 > /sys/class/graphics/fbcon/cursor_blink
