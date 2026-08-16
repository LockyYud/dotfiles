# do-duy dotfiles

Personal dotfiles for an Ubuntu 24.04 desktop. `main` remains the X11/bspwm
baseline while `wayland-niri` contains the Niri migration.
Applications read config from `~/.config` as usual; this repo is the
source of truth that `bootstrap.sh` links into place.

## Managed config

Whole-directory symlinks:

- `bspwm`
- `sxhkd`
- `polybar`
- `picom`
- `kitty`
- `rofi`
- `nvim` (snapshot, not a fork of upstream)
- `fish`
- `ranger`
- `betterlockscreen`
- `networkmanager-dmenu`
- `neofetch`
- `niri`, `waybar`, `swaync`, `swaylock`, `swayidle`

Individual user units under `~/.config/systemd/user` and the Niri helper
scripts under `~/.local/bin` are linked individually. Existing snap-managed
user units are left untouched.

## Not managed (intentionally)

- Browser/editor state: `google-chrome`, `chromium`, `Code`, `Cursor`,
  `Antigravity`, `browseruse`, cookies, trust tokens, session/cache data.
- Credentials: `secrets/`, `gh/hosts.yml`, `ngrok/ngrok.yml`, and all
  `eww/AI/*_api_key` / `*_database_id` files.
- `eww` and its AI widgets — retired during the Niri migration; local runtime
  data is never deleted by bootstrap.
- `wal` — intentionally not adopted.
- `systemd/user` — the units present (`snap.*`) are managed by snapd, not
  authored by hand, so there's nothing user-owned to track. If you add a
  real user unit later, bring it into scope explicitly.
- Any nested `.git` directories (e.g. `eww/AI/.venv/.git`) and generated
  files (`__pycache__`, `*.pyc`, `*.log`).

See `.gitignore` for the full list.

## Install / update

```sh
~/dotfiles/bootstrap.sh
```

- Does not delete existing config: replaced targets are moved into
  `~/.config/.pre-dotfiles-backup/<timestamp>/` before linking.
- Idempotent: re-running when a symlink is already correct is a no-op.
- Only touches entries in its manifest — nothing else in `~/.config` is
  modified.

Flags:

```sh
~/dotfiles/bootstrap.sh --dry-run          # preview link/backup actions, no changes
~/dotfiles/bootstrap.sh --only bspwm       # link/update a single entry
~/dotfiles/bootstrap.sh --only wayland     # link every Niri desktop component
~/dotfiles/bootstrap.sh --only niri        # link one component
```

`--only` must match a manifest entry exactly (or the literal `wayland` group);
anything else exits non-zero without touching `~/.config`.

## Machine-specific settings (host profiles)

`bspwmrc` and `bspwm/apply-display.sh` load
`~/.config/bspwm/hosts/$(hostname).conf` if it exists, for:

- `BSPWM_PRIMARY` / `BSPWM_SECONDARY` / `BSPWM_TERTIARY` — output names
  for a known monitor/dock layout.
- `BSPWM_WALLPAPERS` — space-separated wallpaper paths (feh).

With no matching profile (unknown hostname, or a fresh machine), both
scripts fall back to a single safe monitor and skip the wallpaper step
rather than referencing a hardcoded path.

To add a new machine, copy `bspwm/hosts/DuyDM-PC.conf` to
`bspwm/hosts/<hostname>.conf` and adjust the values — `hostname` must
match exactly.

## Niri on Ubuntu 24.04

Install the pinned compositor stack separately, then link config:

```sh
./install/ubuntu-24.04-wayland.sh --check
./install/ubuntu-24.04-wayland.sh --install
./bootstrap.sh --only wayland
systemctl --user enable niri-waybar niri-swaync niri-vicinae niri-idle niri-wallpaper niri-polkit-agent
```

`install/versions.env` pins Niri, Waybar, xwayland-satellite and Vicinae with
the SHA-256 of their upstream release artifacts. Log out and select **Niri**
from GDM. BSPWM remains available as the rollback session.

## Restoring from a backup

If a bootstrap run replaced something you want back:

```sh
ls ~/.config/.pre-dotfiles-backup/          # find the timestamped run
rm ~/.config/<entry>                        # remove the symlink
mv ~/.config/.pre-dotfiles-backup/<ts>/<entry> ~/.config/<entry>
```

## Secrets policy

Never commit real credentials. If a config needs one:

- Prefer environment variables the app already supports.
- Otherwise use a git-ignored `.env`/`*.local` file (already ignored
  repo-wide) and commit a `*.example` alongside it showing the expected
  shape — `*.example` is explicitly un-ignored so templates stay tracked.
- Extend `.gitignore` for any new local secret/runtime file before it's
  ever staged.

As a last-resort guardrail, `.githooks/pre-commit` blocks a commit whose
staged diff matches common secret shapes (OpenAI/GitHub/Google API keys,
Slack tokens, PEM private keys). Enable it once per clone:

```sh
git config core.hooksPath .githooks
```

It's a backstop, not a substitute for keeping real secrets out of the
tree in the first place — bypass with `git commit --no-verify` only for
confirmed false positives.
