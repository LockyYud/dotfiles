#!/bin/dash

. /tmp/bubble_count

result=""

# Prepend AI chat history labels (cached) so bubbly shows a full conversation.
history_cache="$HOME/.config/eww/AI/data/bubbly_history_cache.yuck"
history=""
if [ -f "$history_cache" ]; then
  history="$(cat "$history_cache")"
fi

# only show the current typing buffer (one live bubble), not empty old slots
filename="/tmp/xkb$bubble_count"
bytecount=$(wc -L <"$filename" 2>/dev/null || echo 0)
width=$((bytecount * 10 + 40))
[ "$width" -gt 530 ] && width=530
[ "$width" -lt 80 ] && width=80
txt="$(cat "$filename" 2>/dev/null) _"
result="(label :class 'label label-user' :text '$txt' :wrap true :width '$width' :xalign 0 :halign 'start')"

echo "(box :orientation 'v' :space-evenly false :class 'chats' $history $result )"
