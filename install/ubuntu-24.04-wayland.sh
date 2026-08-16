#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=versions.env
source "$ROOT_DIR/install/versions.env"

MODE=check
case "${1:---check}" in
  --check) MODE=check ;;
  --dry-run) MODE=dry-run ;;
  --install) MODE=install ;;
  *) echo "usage: $0 [--check|--dry-run|--install]" >&2; exit 2 ;;
esac

require_checksum() {
  local name="$1" value="$2"
  if [ -z "$value" ]; then
    echo "error: $name checksum is empty in install/versions.env" >&2
    exit 1
  fi
}

check_command() { command -v "$1" >/dev/null 2>&1; }

apt_packages=(
  build-essential clang curl git pkg-config meson ninja-build cmake
  libudev-dev libgbm-dev libxkbcommon-dev libegl1-mesa-dev libwayland-dev
  libinput-dev libdbus-1-dev libsystemd-dev libseat-dev libpipewire-0.3-dev
  libpango1.0-dev libdisplay-info-dev libxcb1-dev libxcb-cursor-dev xwayland
  libcairo2-dev libspdlog-dev libfmt-dev libgtkmm-3.0-dev libjsoncpp-dev
  libnl-3-dev libnl-genl-3-dev libmpdclient-dev libpulse-dev libssl-dev
  libgobject-introspection-dev libgirepository1.0-dev
  sway-notification-center swayidle swaylock swaybg grim slurp wl-clipboard
  playerctl brightnessctl network-manager-gnome blueman policykit-1-gnome jq
)

check() {
  printf 'Niri target: %s\nWaybar target: %s\nVicinae target: %s\n' "$NIRI_VERSION" "$WAYBAR_VERSION" "$VICINAE_VERSION"
  printf 'OS: '; . /etc/os-release; printf '%s\n' "$PRETTY_NAME"
  check_command cargo || echo 'missing: cargo (install rustup stable before --install)'
  check_command niri && niri --version || true
  check_command waybar && waybar --version || true
  check_command xwayland-satellite && xwayland-satellite --version || true
  [ -x /usr/local/share/wayland-sessions/niri.desktop ] && echo 'Niri GDM session: installed' || echo 'Niri GDM session: missing'
}

download_verify() {
  local url="$1" destination="$2" expected="$3"
  curl --fail --location --proto '=https' --tlsv1.2 "$url" -o "$destination"
  printf '%s  %s\n' "$expected" "$destination" | sha256sum --check --status
}

install_all() {
  require_checksum NIRI_SHA256 "$NIRI_SHA256"
  require_checksum XWAYLAND_SATELLITE_SHA256 "$XWAYLAND_SATELLITE_SHA256"
  require_checksum WAYBAR_SHA256 "$WAYBAR_SHA256"
  require_checksum VICINAE_SHA256 "$VICINAE_SHA256"
  check_command cargo || { echo 'Install Rust stable with rustup first.' >&2; exit 1; }
  sudo -v

  sudo apt-get update
  sudo apt-get install -y "${apt_packages[@]}"

  local build_dir
  build_dir="$(mktemp -d)"
  trap 'rm -rf "$build_dir"' EXIT

  download_verify "https://github.com/niri-wm/niri/archive/refs/tags/${NIRI_VERSION}.tar.gz" "$build_dir/niri.tar.gz" "$NIRI_SHA256"
  tar -C "$build_dir" -xzf "$build_dir/niri.tar.gz"
  (cd "$build_dir/niri-${NIRI_VERSION#v}" && cargo build --release --locked)
  sudo install -Dm755 "$build_dir/niri-${NIRI_VERSION#v}/target/release/niri" /usr/local/bin/niri
  sudo install -Dm755 "$build_dir/niri-${NIRI_VERSION#v}/resources/niri-session" /usr/local/bin/niri-session
  sudo install -Dm644 "$build_dir/niri-${NIRI_VERSION#v}/resources/niri.desktop" /usr/local/share/wayland-sessions/niri.desktop
  sudo install -Dm644 "$build_dir/niri-${NIRI_VERSION#v}/resources/niri-portals.conf" /usr/local/share/xdg-desktop-portal/niri-portals.conf
  sudo install -Dm644 "$build_dir/niri-${NIRI_VERSION#v}/resources/niri.service" /etc/systemd/user/niri.service
  sudo install -Dm644 "$build_dir/niri-${NIRI_VERSION#v}/resources/niri-shutdown.target" /etc/systemd/user/niri-shutdown.target

  download_verify "https://github.com/Supreeeme/xwayland-satellite/archive/refs/tags/${XWAYLAND_SATELLITE_VERSION}.tar.gz" "$build_dir/satellite.tar.gz" "$XWAYLAND_SATELLITE_SHA256"
  tar -C "$build_dir" -xzf "$build_dir/satellite.tar.gz"
  (cd "$build_dir/xwayland-satellite-${XWAYLAND_SATELLITE_VERSION#v}" && cargo build --release --locked)
  sudo install -Dm755 "$build_dir/xwayland-satellite-${XWAYLAND_SATELLITE_VERSION#v}/target/release/xwayland-satellite" /usr/local/bin/xwayland-satellite

  download_verify "https://github.com/Alexays/Waybar/archive/refs/tags/${WAYBAR_VERSION}.tar.gz" "$build_dir/waybar.tar.gz" "$WAYBAR_SHA256"
  tar -C "$build_dir" -xzf "$build_dir/waybar.tar.gz"
  meson setup "$build_dir/waybar-build" "$build_dir/Waybar-${WAYBAR_VERSION}" -Dniri=enabled
  meson compile -C "$build_dir/waybar-build"
  sudo meson install -C "$build_dir/waybar-build"

  mkdir -p "$HOME/.local/lib/vicinae" "$HOME/.local/bin"
  download_verify "https://github.com/vicinaehq/vicinae/releases/download/${VICINAE_VERSION}/Vicinae-x86_64.AppImage" "$HOME/.local/lib/vicinae/Vicinae-${VICINAE_VERSION}.AppImage" "$VICINAE_SHA256"
  chmod 0755 "$HOME/.local/lib/vicinae/Vicinae-${VICINAE_VERSION}.AppImage"
  ln -sfn "$HOME/.local/lib/vicinae/Vicinae-${VICINAE_VERSION}.AppImage" "$HOME/.local/bin/vicinae"

  sudo systemctl daemon-reload
  systemctl --user daemon-reload
  echo 'Install complete. Run bootstrap.sh --only wayland, then enable the listed user units.'
}

case "$MODE" in
  check) check ;;
  dry-run) printf 'Would install apt packages:\n%s\n' "${apt_packages[*]}"; check ;;
  install) install_all ;;
esac
