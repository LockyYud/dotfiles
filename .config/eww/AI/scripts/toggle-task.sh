#!/bin/sh

task_id="$1"

if [ -z "$task_id" ]; then
  exit 0
fi

~/.config/eww/AI/assistant.py task_toggle "$task_id" >/dev/null 2>&1

