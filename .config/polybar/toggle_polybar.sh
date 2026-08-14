#!/usr/bin/env bash
set -euo pipefail

if command -v polybar-msg >/dev/null 2>&1 && polybar-msg cmd toggle >/dev/null 2>&1; then
    exit 0
fi

if pgrep -u "$UID" -x polybar >/dev/null; then
    pkill -u "$UID" -x polybar
else
    "$HOME/.config/polybar/launch.sh"
fi
