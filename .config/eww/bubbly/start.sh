#!/bin/dash

basedir="$HOME/.config/eww/bubbly"

chat() {
	mon="$(bspc query -M -m pointed --names 2>/dev/null | head -n1)"
	[ -z "$mon" ] && mon=0
	eww -c "$basedir/bubbles" open bubbly --screen "$mon" &
}

keystrokes() {
	rm -f /tmp/bubbly_keys
	mon="$(bspc query -M -m pointed --names 2>/dev/null | head -n1)"
	[ -z "$mon" ] && mon=0
	eww -c "$basedir/keystrokes" open keystrokes --screen "$mon"
	"$basedir/keystrokes/scripts/getkeys.sh"
}

if [ $# -eq 0 ]; then
 	eww -c "$basedir/selector" open selector
fi

"$@"
