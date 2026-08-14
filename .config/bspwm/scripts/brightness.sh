#!/usr/bin/env bash
set -euo pipefail

direction="${1:-up}"

if ! command -v brightnessctl >/dev/null 2>&1; then
    notify-send "Brightness" "brightnessctl is not installed" 2>/dev/null || \
        printf '%s\n' "brightnessctl is not installed" >&2
    exit 1
fi

case "$direction" in
    up)
        brightnessctl set +5%
        ;;
    down)
        brightnessctl set 5%-
        ;;
    *)
        brightnessctl set "$direction"
        ;;
esac

"$HOME/.config/eww/Misc/scripts/brightness" >/dev/null 2>&1 || true
