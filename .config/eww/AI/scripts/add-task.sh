#!/bin/sh

# If text passed as arguments, use it; otherwise prompt via rofi/dmenu if available.
text="$*"

if [ -z "$text" ]; then
  if command -v rofi >/dev/null 2>&1; then
    text="$(printf '' | rofi -dmenu -p 'New task')"
  elif command -v dmenu >/dev/null 2>&1; then
    text="$(printf '' | dmenu -p 'New task')"
  fi
fi

if [ -z "$text" ]; then
  exit 0
fi

~/.config/eww/AI/assistant.py task_add "$text" >/dev/null 2>&1


