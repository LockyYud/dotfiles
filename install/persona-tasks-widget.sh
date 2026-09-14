#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIDGET_DIR="$ROOT_DIR/persona-tasks-widget"

MODE=check
case "${1:---check}" in
  --check) MODE=check ;;
  --dry-run) MODE=dry-run ;;
  --install) MODE=install ;;
  *) echo "usage: $0 [--check|--dry-run|--install]" >&2; exit 2 ;;
esac

# gir1.2-gtklayershell-0.1 is the GObject-Introspection typelib for
# gtk-layer-shell — the C library (libgtk-layer-shell0) is not enough on
# its own for Python/PyGObject to use it. libgirepository-2.0-dev is a
# build-time dependency for PyGObject 3.50 (which Fabric pins). Neither is
# documented by Fabric; both were found by actually trying the install.
apt_packages=(libgirepository-2.0-dev gir1.2-gtklayershell-0.1)

check_command() { command -v "$1" >/dev/null 2>&1; }

check() {
  printf 'Widget dir: %s\n' "$WIDGET_DIR"
  check_command python3 && python3 --version || echo 'missing: python3'
  dpkg -s gir1.2-gtklayershell-0.1 >/dev/null 2>&1 \
    && echo 'gir1.2-gtklayershell-0.1: installed' \
    || echo 'gir1.2-gtklayershell-0.1: missing'
  [ -x "$WIDGET_DIR/venv/bin/python" ] \
    && echo 'venv: present' \
    || echo 'venv: missing (run --install)'
}

install_all() {
  sudo apt-get update
  sudo apt-get install -y "${apt_packages[@]}"

  python3 -m venv "$WIDGET_DIR/venv"
  "$WIDGET_DIR/venv/bin/pip" install --upgrade pip
  "$WIDGET_DIR/venv/bin/pip" install -r "$WIDGET_DIR/requirements.txt"

  echo 'Install complete. Run bootstrap.sh --only wayland, then:'
  echo '  systemctl --user enable --now persona-tasks-widget'
}

case "$MODE" in
  check) check ;;
  dry-run) printf 'Would install apt packages:\n%s\n' "${apt_packages[*]}"; check ;;
  install) install_all ;;
esac
