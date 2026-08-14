#!/bin/sh

CONFIG="$HOME/.config/eww/bubbly/bubbles"

# Toggle bubbly window: if it's open, close; otherwise open on monitor under mouse.
if eww -c "$CONFIG" active-windows 2>/dev/null | grep -q "^bubbly:"; then
  eww -c "$CONFIG" close bubbly
else
  "$HOME/.config/eww/bubbly/start.sh" chat
fi

