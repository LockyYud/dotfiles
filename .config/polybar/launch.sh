#!/usr/bin/env bash

if command -v polybar-msg >/dev/null 2>&1; then
    polybar-msg cmd quit >/dev/null 2>&1 || true
fi

killall -q polybar 2>/dev/null || true
while pgrep -u "$UID" -x polybar >/dev/null; do sleep 0.5; done

# Lấy danh sách các màn hình
mapfile -t monitors < <(polybar --list-monitors 2>/dev/null | cut -d: -f1)

if [ "${#monitors[@]}" -eq 0 ] && command -v xrandr >/dev/null 2>&1; then
    mapfile -t monitors < <(xrandr --listmonitors | awk '/^[[:space:]]*[0-9]+:/ {print $4}')
fi

if [ "${#monitors[@]}" -eq 0 ]; then
    echo "No monitors found; Polybar was not launched." >&2
    exit 1
fi

# Launch polybar cho từng màn hình với config riêng
for ((i=0; i<${#monitors[@]}; i++)); do
    config_index=$((i % 3 + 1))
    MONITOR="${monitors[$i]}" polybar -c "$HOME/.config/polybar/config-monitor${config_index}.ini" main &
    echo "Polybar monitor${config_index} launched on ${monitors[$i]}"
done

echo "Đã khởi chạy Polybar trên tất cả các màn hình"
