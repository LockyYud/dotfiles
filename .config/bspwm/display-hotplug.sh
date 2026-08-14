#!/usr/bin/env bash
set -euo pipefail

apply_display="$HOME/.config/bspwm/apply-display.sh"
last_state=""

while true; do
  state="$(xrandr --query | awk '/^(eDP|DP|HDMI)/ {print $1, $2}')"
  if [ "$state" != "$last_state" ]; then
    sleep 1
    "$apply_display" || true
    last_state="$(xrandr --query | awk '/^(eDP|DP|HDMI)/ {print $1, $2}')"
  fi
  sleep 2
done
