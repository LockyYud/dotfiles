#!/usr/bin/env bash
set -euo pipefail

# Host profile provides BSPWM_PRIMARY/SECONDARY/TERTIARY output names for
# machines with a known dock/monitor layout. Without a matching profile
# (or on an unknown host) this falls back to a single safe monitor.
HOST_PROFILE="$HOME/.config/bspwm/hosts/$(hostname).conf"
BSPWM_PRIMARY=""
BSPWM_SECONDARY=""
BSPWM_TERTIARY=""
[ -f "$HOST_PROFILE" ] && . "$HOST_PROFILE"

PRIMARY="${BSPWM_PRIMARY:-eDP-1}"
SECONDARY="${BSPWM_SECONDARY:-}"
TERTIARY="${BSPWM_TERTIARY:-}"

connected() {
  [ -n "$1" ] && xrandr --query | grep -q "^$1 connected"
}

first_connected_output() {
  xrandr --query | awk '/ connected/ {print $1; exit}'
}

disable_disconnected_outputs() {
  local args=()
  while read -r output; do
    [ -n "$output" ] && args+=(--output "$output" --off)
  done < <(xrandr --query | awk '/ disconnected/ {print $1}')

  if [ "${#args[@]}" -gt 0 ]; then
    xrandr "${args[@]}" || true
  fi
}

sync_bspwm_monitors() {
  local monitors
  monitors="$(bspc query -M --names 2>/dev/null || true)"

  monitor_exists() {
    [ -n "$1" ] && printf '%s\n' "$monitors" | grep -qx "$1"
  }

  if monitor_exists "$PRIMARY" && monitor_exists "$SECONDARY" && monitor_exists "$TERTIARY"; then
    bspc wm -O "$PRIMARY" "$SECONDARY" "$TERTIARY"
    bspc monitor "$PRIMARY" -d 1 2
    bspc monitor "$SECONDARY" -d 3 4
    bspc monitor "$TERTIARY" -d 5 6
  elif monitor_exists "$PRIMARY" && monitor_exists "$SECONDARY"; then
    bspc wm -O "$PRIMARY" "$SECONDARY"
    bspc monitor "$PRIMARY" -d 1 2 3
    bspc monitor "$SECONDARY" -d 4 5 6
  elif monitor_exists "$PRIMARY" && monitor_exists "$TERTIARY"; then
    bspc wm -O "$PRIMARY" "$TERTIARY"
    bspc monitor "$PRIMARY" -d 1 2 3
    bspc monitor "$TERTIARY" -d 4 5 6
  elif monitor_exists "$SECONDARY"; then
    bspc monitor "$SECONDARY" -d 1 2 3 4 5 6
  elif monitor_exists "$TERTIARY"; then
    bspc monitor "$TERTIARY" -d 1 2 3 4 5 6
  elif monitor_exists "$PRIMARY"; then
    bspc monitor "$PRIMARY" -d 1 2 3 4 5 6
  else
    local first_monitor
    first_monitor="$(printf '%s\n' "$monitors" | sed -n '1p')"
    [ -n "$first_monitor" ] && bspc monitor "$first_monitor" -d 1 2 3 4 5 6
  fi
}

if ! command -v xrandr >/dev/null 2>&1; then
  exit 0
fi

disable_disconnected_outputs

# If the profile's primary isn't actually present on this hardware (no
# profile, wrong host, docked/undocked), fall back to whatever xrandr
# reports as connected instead of forcing a nonexistent output name.
if ! connected "$PRIMARY"; then
  PRIMARY="$(first_connected_output)"
  SECONDARY=""
  TERTIARY=""
fi

if [ -n "$PRIMARY" ]; then
  if connected "$SECONDARY" && connected "$TERTIARY"; then
    xrandr --output "$PRIMARY" --primary --auto --pos 0x0 \
           --output "$SECONDARY" --auto --pos 1920x0 \
           --output "$TERTIARY" --auto --pos 3840x0
  elif connected "$SECONDARY"; then
    xrandr --output "$PRIMARY" --primary --auto --pos 0x0 \
           --output "$SECONDARY" --auto --right-of "$PRIMARY"
  elif connected "$TERTIARY"; then
    xrandr --output "$PRIMARY" --primary --auto --pos 0x0 \
           --output "$TERTIARY" --auto --right-of "$PRIMARY"
  else
    xrandr --output "$PRIMARY" --primary --auto
  fi
fi

sleep 0.2
sync_bspwm_monitors

if [ "${1:-}" != "--no-polybar" ] && [ -x "$HOME/.config/polybar/launch.sh" ]; then
  "$HOME/.config/polybar/launch.sh" &
fi
