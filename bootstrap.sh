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

# True if $1 (a manifest entry) should be processed given --only. With no
# --only, everything matches. "--only eww" is a meta-token covering every
# eww_entries child; anything else must match a manifest entry exactly.
entry_matches() {
    local rel="$1"
    [ -z "$ONLY" ] && return 0
    [ "$rel" = "$ONLY" ] && return 0
    if [ "$ONLY" = "eww" ]; then
        case "$rel" in
            eww/*) return 0 ;;
        esac
    fi
    return 1
}

validate_only() {
    [ -z "$ONLY" ] && return 0
    [ "$ONLY" = "eww" ] && return 0
    local rel
    for rel in "${core_entries[@]}" "${eww_entries[@]}"; do
        [ "$rel" = "$ONLY" ] && return 0
    done
    echo "error: --only \"$ONLY\" does not match any manifest entry" >&2
    echo "run with --dry-run (no --only) to list valid entries" >&2
    exit 1
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
    local rel matched=0
    for rel in $(all_entries); do
        entry_matches "$rel" || continue
        matched=1
        printf '  %s\n' "$rel"
    done
    [ "$matched" -eq 1 ] || printf '  (none match "%s")\n' "$ONLY"
    printf '\n'
}

main() {
    validate_only
    preflight

    local rel
    for rel in "${core_entries[@]}"; do
        entry_matches "$rel" || continue
        link_entry "$rel"
    done

    for rel in "${eww_entries[@]}"; do
        entry_matches "$rel" || continue
        link_entry "$rel"
    done

    if [ "$DRY_RUN" -eq 1 ]; then
        printf '\nDry run complete. No changes were made.\n'
    else
        printf '\nDone. Backups, if any, are in %s\n' "$BACKUP_DIR"
    fi
}

main
