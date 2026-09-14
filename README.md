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

## Persona Assistant desktop touch points

Task data lives only in Persona Assistant (`~/personal/persona-assistant`);
these are just glance/keyboard entry points into it, backed by a desktop
token (never the worker's full BFF secret):

- `persona-tasks-widget/` is the primary day-to-day surface: a small
  always-visible GTK3 layer-shell panel (Fabric), floating top-right on
  every output. `Routines` leads, then task **status** — `In progress`, then a
  collapsible `Open` — with Start/Stop and Complete buttons right on it
  (view-and-act only for now — quick-add is temporarily removed from the
  widget; use the webapp/chat to create tasks). `▶` moves a task to
  in-progress, `⏸` moves it back; both write through to the task's Notion
  `Status`.

  **Every block's third line is today's minutes**, not a schedule. Duration
  chips (`30m` `1h` `2h` `⋯`) commit part of today to that task, on ordinary
  tasks as much as on routines — deciding what today holds is the thing done
  every morning, and having one control for both kinds of work is one habit
  rather than two. This replaced the old due-time-plus-snooze row, so **snooze
  is gone from the panel**; the deadline itself was not lost, it moved up into
  the block's head (`2d late`, in red when overdue) where it costs no room,
  and each section header still carries its `N overdue` badge. The schedule
  also still orders tasks inside each section (overdue → today → future →
  undated). (Before this, sections *were*
  the schedule, which silently equated "scheduled" with "in progress" —
  these tasks carry a real status, so it is read rather than inferred.) A
  task with subtasks renders as one block — ticket key, `work`/`personal`, a
  subtask meter (`●●○ 2/3`), then its next subtask indented beneath, which is
  the row that carries the ✓; the parent's own ✓ stays inert until its
  subtasks are done. Subtasks never appear as top-level rows. The footer's
  `synced Nm ago` turns red when polling fails, so a stale list is never
  mistaken for an empty one.

  **Routines** are tasks pursued at a rate per month (a `Monthly Target (h)`
  in Notion) rather than finished once, and they get their own section
  because the actions that suit them are not the ones that suit a dated
  task: the ✓ on a task block completes the *task*, which for a routine
  would mean finishing it forever, and the snooze chips push a `dueAt` onto
  something that has none by design. So a routine block shows its month
  instead of a deadline — `behind 3h40m · 1h/20h`, `day 7/30` — with
  the same duration chips every other block has. Exactly one chip is lit, and
  it always means *this is what today is set to*; only when nothing is set does
  the highlight fall on the pace's suggestion, which is then the obvious click.
  (Lighting the suggestion while a different amount was already planned made
  the row contradict the line under it — "30m planned today" beneath a lit-up
  53m reads as though the 53m had been chosen.) The suggestion still leads the
  row when it is not one of the fixed options, so it stays one click away
  either way. Today's session is the actionable line indented
  beneath, the same way a subtask is: `1h planned today` with a ✓ that
  credits those minutes and a `⊘` that skips the day deliberately — a
  deliberate skip does not count against the month, whereas letting the day
  pass silently does. `N behind this month` under the section header is the
  only signal a routine can produce, having no deadline to go overdue
  against, so it stays visible without hovering. `⟳` on **any** block — routine
  or not — opens an hours-per-month picker, which is the only way outside chat
  to make a task a routine; setting a target also starts the task, since a
  routine only reaches this panel while it is `in_progress`, and clearing one
  leaves the task running (no longer measuring something monthly is not the
  same as no longer doing it). Both chip rows end in `⋯`, which swaps them for
  a text field — minutes today (`90`, `1h30`, `45m`) or hours per month (`20`,
  `20.5`) — committed with Enter, cancelled with Escape, and marked red rather
  than cleared when the value will not parse. While a field is open the 30s
  poll keeps its data but skips the re-render, since rebuilding the widgets
  would destroy what you were halfway through typing. Routines are exempt
  from
  the panel's `MAX_TASKS_SHOWN` cap and count toward the header's `NOW · N`,
  since a routine is in progress by definition. Set
  `PERSONA_PANEL_FIXTURE=some-panel.json` to render a saved
  `{"now": ..., "today": ...}` document without a worker or a token — the
  only way to exercise this layout before any routine exists. Toggle
  show/hide with `Mod+Shift+T` or by left-clicking `custom/persona-tasks` in
  Waybar — both send `SIGUSR1` to the running widget process via
  `persona-tasks-widget-toggle`; the systemd service itself keeps running,
  only the window visibility flips. Setup (one-time, needs `sudo` for two
  apt packages so run interactively):
  ```sh
  ./install/persona-tasks-widget.sh --install   # apt deps + venv, prints next steps
  ./bootstrap.sh --only wayland                 # symlinks the dir + systemd unit + toggle script
  systemctl --user enable --now persona-tasks-widget
  ```
  Polls the worker every 30s; edits under `persona-tasks-widget/` take effect
  on `systemctl --user restart persona-tasks-widget` (no rebuild step, unlike
  the Vicinae extension). If Waybar was already running when you add/change
  the `on-click` wiring, restart it too (`systemctl --user restart
  niri-waybar`) — it doesn't hot-reload click bindings.
- `custom/persona-tasks` in Waybar (`.config/waybar/scripts/persona-tasks-status`)
  reports a status every 60s, never a task title — `▶ 2 doing · 3h`,
  `▶ 3 · ! 2 late`, `! 1 overdue`, `✓ 0 · 4 open`, `✓ all clear` — capped at
  ~26 characters so it can't push `niri/window` out of the bar's centre. It
  groups by status the same way the widget does, so the bar and the panel
  can't disagree about how much is in progress — routines included, which is
  why the counts moved: they are `in_progress` by definition but live outside
  every schedule bucket, so leaving them out undercounted "doing". Full
  titles, subtask progress, deadlines and each routine's month
  (`• Học tiếng Anh — 1h/20h (behind 3h40m), today 48m`) are in its tooltip. Set
  `PERSONA_TASKS_FIXTURE=some-response.json` to render a saved
  `/desktop/tasks/now` payload without touching the worker, which is how the
  doing/overdue/open/all-clear branches get exercised. Left-click toggles
  the widget above,
  right-click opens the full webapp in a browser tab (chat/settings/full
  list — the things the widget doesn't cover).
- `vicinae-extensions/my-tasks/` is a Vicinae extension (reachable via
  `Mod+D`) with two commands. **My Tasks** lists overdue/today/next-up with
  Open/Complete/Snooze, plus a `Routines` section carrying each one's pace —
  present so this view cannot report "all clear" while a routine sits weeks
  behind its month. **Plan Today** is the day planner: today's sessions and
  every routine still wanting time, with duration presets, a `Custom…` form
  that parses `90` / `1h30` / `45m`, and `Done` / `Done, but shorter…` /
  `Skip today` on a planned session. Both commands can set or clear a task's
  monthly target (hours per month, with a `Custom…` form), so a routine can be
  created without opening chat. It is **not** bootstrap-managed
  — `vici build` installs it directly to
  `~/.local/share/vicinae/extensions/persona-my-tasks`. Rebuild after editing:
  ```sh
  cd vicinae-extensions/my-tasks && npm install && npx vici build
  ```
- `persona-tasks-open` (`.local/bin/`) opens the webapp as a chrome-less
  Chrome app-mode window (`--tab` for a regular tab). No longer bound to a
  default keybind — daily use is the widget above; run it manually if you
  want the app-mode window for some reason.

All four read `~/.config/persona-assistant/config.env` (`PERSONA_WORKER_URL`,
`PERSONA_APP_URL`) and `~/.config/persona-assistant/desktop-token` (0600),
both outside this repo, never committed. Set up from Persona Assistant's web
UI at `/settings` ("Connect this desktop"), then run:

```sh
persona-connect   # paste the token shown once, then App/Worker URLs; only run from `wayland_bin_entries`
```

If the token file is missing or the token is revoked, Waybar shows
`Tasks · reconnect` and the widget shows a "run persona-connect" message
instead of crashing.

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
