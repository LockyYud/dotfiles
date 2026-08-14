#!/bin/sh

CONFIG="$HOME/.config/eww"

# Try to close first; if it wasn't open, open it on the monitor under the mouse.
if ! eww -c "$CONFIG" close ai-tasks-window 2>/dev/null; then
  MON="$(bspc query -M -m pointed --names 2>/dev/null | head -n1)"
  [ -z "$MON" ] && MON=0
  eww -c "$CONFIG" open ai-tasks-window --screen "$MON"
fi
