#!/usr/bin/env bash
set -euo pipefail

theme="$HOME/.config/rofi/themes/emojimenu.rasi"

selection=$(
    printf '%s\n' \
        "😀 grinning face" \
        "😂 face with tears of joy" \
        "😊 smiling face" \
        "😍 heart eyes" \
        "🤔 thinking face" \
        "😎 sunglasses" \
        "😭 loudly crying" \
        "🔥 fire" \
        "✨ sparkles" \
        "✅ check mark" \
        "❌ cross mark" \
        "⚠️ warning" \
        "💡 idea" \
        "🚀 rocket" \
        "❤️ red heart" \
        "👍 thumbs up" \
        "🙏 folded hands" \
        "🎉 party popper" |
        rofi -dmenu -i -p "Emoji" -theme "$theme"
)

[ -n "$selection" ] || exit 0

emoji="${selection%% *}"
printf '%s' "$emoji" | xclip -selection clipboard
notify-send "Emoji copied" "$emoji" >/dev/null 2>&1 || true
