#!/usr/bin/env bash
set -euo pipefail

DOTFILES_DIR="${DOTFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
BACKUP_DIR="$CONFIG_DIR/.pre-dotfiles-backup/$(date +%Y%m%d-%H%M%S)"
DRY_RUN=0
ONLY=""

usage() {
    cat <<'EOF'
Usage: bootstrap.sh [--dry-run] [--only <entry>] [-h|--help]

  --dry-run        Preview what would be linked/backed up; make no changes.
  --only <entry>   Only process one manifest entry (e.g. bspwm, eww, nvim).
  -h, --help       Show this help.

With no options, bootstrap symlinks every managed entry into
$XDG_CONFIG_HOME (or ~/.config), backing up anything replaced.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --only)
            [ "$#" -ge 2 ] || { echo "error: --only requires an argument" >&2; exit 1; }
            ONLY="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

backup_target() {
    local target="$1"
    local rel="$2"

    if [ "$DRY_RUN" -eq 1 ]; then
        printf 'would back up %s -> %s/%s\n' "$target" "$BACKUP_DIR" "$rel"
        return 0
    fi

    mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
    mv "$target" "$BACKUP_DIR/$rel"
    printf 'backed up %s -> %s\n' "$target" "$BACKUP_DIR/$rel"
}

link_entry() {
    local rel="$1"
    local source="$DOTFILES_DIR/.config/$rel"
    local target="$CONFIG_DIR/$rel"

    if [ ! -e "$source" ] && [ ! -L "$source" ]; then
        printf 'skip missing source %s\n' "$source" >&2
        return 0
    fi

    if [ -L "$target" ] && [ "$(readlink "$target")" = "$source" ]; then
        printf 'ok %s\n' "$target"
        return 0
    fi

    if [ -e "$target" ] || [ -L "$target" ]; then
        backup_target "$target" "$rel"
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        printf 'would link %s -> %s\n' "$target" "$source"
        return 0
    fi

    mkdir -p "$(dirname "$target")"
    ln -s "$source" "$target"
    printf 'linked %s -> %s\n' "$target" "$source"
}

# Manifest of top-level entries under .config/ that get symlinked whole.
core_entries=(
    bspwm
    sxhkd
    polybar
    picom
    kitty
    rofi
    nvim
    fish
    ranger
    betterlockscreen
    networkmanager-dmenu
    neofetch
)

# eww is linked in hybrid mode: only the config/source files below are
# managed, so local API keys, venvs, runtime data, logs, and task state
# under .config/eww stay untouched.
eww_entries=(
    eww/Main
    eww/Player
    eww/System-Menu
    eww/Misc
    eww/bubbly
    eww/eww.scss
    eww/eww.yuck
    eww/AI/assistant.py
    eww/AI/scripts
    eww/AI/eww.scss
    eww/AI/eww.yuck
    eww/AI/pyproject.toml
    eww/AI/uv.lock
)

all_entries() {
    printf '%s\n' "${core_entries[@]}" "${eww_entries[@]}"
}

preflight() {
    printf 'dotfiles source: %s\n' "$DOTFILES_DIR"
    printf 'config target:   %s\n' "$CONFIG_DIR"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf 'mode:            dry-run (no changes will be made)\n'
    fi
    if [ -n "$ONLY" ]; then
        printf 'scope:           only "%s"\n' "$ONLY"
    fi
    printf '\nentries to process:\n'
    if [ -n "$ONLY" ]; then
        all_entries | grep -x -e "$ONLY" -e "$ONLY/.*" || printf '  (none match "%s")\n' "$ONLY"
    else
        all_entries | sed 's/^/  /'
    fi
    printf '\n'
}

main() {
    preflight

    for rel in "${core_entries[@]}"; do
        if [ -n "$ONLY" ] && [ "$rel" != "$ONLY" ]; then
            continue
        fi
        link_entry "$rel"
    done

    if [ -z "$ONLY" ] || [ "$ONLY" = "eww" ]; then
        mkdir -p "$CONFIG_DIR/eww" "$CONFIG_DIR/eww/AI"
        for rel in "${eww_entries[@]}"; do
            link_entry "$rel"
        done
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        printf '\nDry run complete. No changes were made.\n'
    else
        printf '\nDone. Backups, if any, are in %s\n' "$BACKUP_DIR"
    fi
}

main
