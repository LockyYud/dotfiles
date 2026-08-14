#!/bin/sh

text="$*"

CONFIG_MAIN="$HOME/.config/eww"
CONFIG_BUBBLY="$HOME/.config/eww/bubbly/bubbles"
ASSIST="$HOME/.config/eww/AI/assistant.py"

case "$text" in
  /exit)
    # Close chat window
    eww -c "$CONFIG_BUBBLY" close bubbly
    ;;
  /clear)
    "$ASSIST" clear_history
    ;;
  /help)
    "$ASSIST" inject_help
    ;;
  *)
    # Clear input immediately for better UX
    eww -c "$CONFIG_BUBBLY" update chat-input=''
    # Run assistant in background
    [ -n "$text" ] && "$ASSIST" chat "$text" >>"$HOME/.config/eww/AI/assistant.log" 2>&1 &
    ;;
esac

