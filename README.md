# do-duy dotfiles

Personal dotfiles for the bspwm/X11 desktop, symlinked into `~/.config`.
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

Hybrid symlink (only listed paths are managed):

- `eww` — config/source files only. Local API keys, the `AI/.venv`
  virtual environment, runtime data, logs, and task state stay in
  `~/.config/eww` and are never tracked.

## Not managed (intentionally)

- Browser/editor state: `google-chrome`, `chromium`, `Code`, `Cursor`,
  `Antigravity`, `browseruse`, cookies, trust tokens, session/cache data.
- Credentials: `secrets/`, `gh/hosts.yml`, `ngrok/ngrok.yml`, and all
  `eww/AI/*_api_key` / `*_database_id` files.
- `wal` — currently empty; will likely be replaced by Matugen during the
  Wayland migration instead of being adopted here.
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
~/dotfiles/bootstrap.sh --dry-run       # preview link/backup actions, no changes
~/dotfiles/bootstrap.sh --only bspwm    # link/update a single entry
~/dotfiles/bootstrap.sh --only eww      # re-run just the eww hybrid entries
```

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
- Otherwise use a git-ignored `*.local` (or similarly patterned) file and
  commit a `*.example` alongside it showing the expected shape.
- Extend `.gitignore` for any new local secret/runtime file before it's
  ever staged.
