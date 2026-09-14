"""
Always-visible Persona Assistant task panel — a small GTK3 layer-shell
overlay (top-right, non-exclusive so it never steals screen space like
Waybar does) built with Fabric. Reads/writes tasks through api_client.py,
which talks to the same worker /desktop/* routes as the Vicinae "My Tasks"
extension, using the same desktop token and config.env.

Toggled on/off by sending SIGUSR1 to this process — see
persona-tasks-widget-toggle, wired to Waybar's left-click and Mod+Shift+T.

Information architecture (Today Plan v1) — the widget is a TODAY EXECUTION
SURFACE, not a task manager. TODAY is the first, primary section: it renders
`today.sessions` directly, ordered by `position`, not something derived from
walking every task. A task can carry several Today items on the same day now
(Today Plan v1 dropped the old one-session-per-task-per-day assumption), so
grouping here is by *session*, never by task.

  NEXT           the first still-planned item — not "current"/"running":
                 the domain has no started/running session, so this is only
                 the next thing up, not a claim about what you're doing.
  LATER          the rest of today's planned items, in position order.
  NEEDS ATTENTION routines behind pace, and due/overdue tasks, that have
                 nothing planned for today yet — the reason to open the
                 composer, not a second task list.
  Browse tasks   In progress / Open, collapsed by default. This is the old
                 status-grouped view, kept as the place to go looking for
                 something to add to Today, not the panel's main event.

A task with subtasks still renders as ONE block in Browse: the parent is
context (ticket badge, title, subtask meter) and its nextStep is the
actionable line beneath it. Subtasks never appear as top-level rows; anything
carrying parentTaskId is filtered out.

Deliberately out of scope for this pass: dragging Today items to reorder
(the worker has no reorder endpoint yet — only `position` as assigned on
create) and editing a session's `startAt` from the composer (view-only here).
"""

import re
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from fabric import Application
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.wayland import WaylandWindow

import api_client

POLL_INTERVAL_SECONDS = 30
WINDOW_WIDTH = 400
# The body scrolls past this, so no task is silently dropped for lack of room.
MAX_BODY_HEIGHT = 420
MAX_TASKS_SHOWN = 20
# A wrapping label inside a GtkScrolledWindow reports its natural height for
# the wrong width (GTK3's height-for-width doesn't survive the scrolled
# window), which clipped the whole widget to one line's worth of height. Give
# the title an explicit pixel width instead, so no height-for-width
# negotiation is needed: window 400 - window padding 16 - content inset 16 -
# block padding 12 - two action buttons and their spacing 68. Every top-level
# block has exactly two (a status toggle and ✓), so this stays uniform.
TITLE_WIDTH = 288
# GTK3 labels have no max-lines, so the text is clamped here to keep a long
# Notion title from growing a block to four lines. ~40 chars fit per line at
# TITLE_WIDTH in 13px Noto Sans.
TITLE_CLAMP_CHARS = 80
# Above this many subtasks, individual pips stop being countable at a glance
# and a proportional bar reads better.
PIP_MAX_TOTAL = 6
METER_BAR_WIDTH = 64

# Durations offered in the Today composer and for a routine's quick-add chip.
# The pace's own suggestion is added in front of these at render time for a
# routine, so the common case is one click on a number that already accounts
# for what is left and how much of the month remains.
PLAN_OPTIONS = [("30m", 30), ("45m", 45), ("1h", 60), ("90m", 90), ("2h", 120)]
# Monthly targets offered when designating a routine. Hours, because that is
# the unit the target is thought in ("20 hours of English a month") even though
# everything downstream stores minutes.
MONTHLY_TARGET_OPTIONS = [("5h", 5 * 60), ("10h", 10 * 60), ("20h", 20 * 60), ("40h", 40 * 60)]
PRIORITY_CLASSES = {"urgent", "high", "medium", "low"}
# How many due/overdue tasks (beyond routines) Needs Attention will surface.
# It is a nudge toward planning today, not a second copy of the backlog.
MAX_ATTENTION_TASKS = 5

STATUS_IN_PROGRESS = "in_progress"
STATUS_OPEN = "open"
# Section order in Browse tasks: what's already started comes before what
# could be picked up. The worker never returns done/cancelled tasks, so these
# two cover everything; anything with an unrecognised status is treated as
# open rather than dropped, so a new status added upstream degrades to
# "visible" instead of "invisible".
STATUS_SECTIONS = [
    (STATUS_IN_PROGRESS, "In progress", "in-progress"),
    (STATUS_OPEN, "Open", "open"),
]

# "TIC26 #31: Nền tảng AI..." → badge "TIC26 #31" + title "Nền tảng AI...".
# Pulling the key out of the title keeps the wrapped text short without
# losing the identifier you actually search Notion by.
TICKET_KEY_PATTERN = re.compile(r"^([A-Z][A-Z0-9]*\s*#\d+)\s*:\s*(\S.*)$", re.DOTALL)


def split_ticket_key(title: str) -> tuple[str | None, str]:
    match = TICKET_KEY_PATTERN.match(title.strip())
    if not match:
        return None, title.strip()
    key, rest = match.group(1), match.group(2)
    return re.sub(r"\s+", " ", key), rest.strip()


def clamp_title(title: str, limit: int = TITLE_CLAMP_CHARS) -> str:
    if len(title) <= limit:
        return title
    cut = title[:limit].rstrip()
    spaced = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return f"{spaced}…"


def relative_deadline(due_at_iso: str) -> str:
    due = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
    diff_minutes = round((due - datetime.now(timezone.utc)).total_seconds() / 60)

    def duration(minutes: int) -> str:
        if minutes < 60:
            return f"{minutes}m"
        hours = round(minutes / 60)
        if hours < 24:
            return f"{hours}h"
        return f"{round(hours / 24)}d"

    if diff_minutes < 0:
        return f"overdue by {duration(-diff_minutes)}"
    if diff_minutes == 0:
        return "due now"
    return f"due in {duration(diff_minutes)}"


def is_overdue(due_at_iso: str) -> bool:
    due = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
    return due < datetime.now(timezone.utc)


def relative_sync_age(synced_at: datetime) -> str:
    seconds = round((datetime.now(timezone.utc) - synced_at).total_seconds())
    if seconds < 60:
        return "synced just now"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"synced {minutes}m ago"
    return f"synced {round(minutes / 60)}h ago"


def format_time_of_day(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return dt.strftime("%H:%M")


def priority_class(priority: str, prefix: str) -> str:
    tone = priority if priority in PRIORITY_CLASSES else "medium"
    return f"{prefix}-{tone}"


def parse_duration_minutes(raw: str) -> int | None:
    """Parses a session length: "90", "1h30", "1h30m", "1h", "45m".

    A bare number means minutes, which is what someone typing "90" intends.
    Returns None rather than guessing, so a typo leaves the field open instead
    of committing a number that was never meant.
    """
    text = raw.strip().lower()
    if not text:
        return None
    if text.isdigit():
        minutes = int(text)
        return minutes if 0 < minutes <= 24 * 60 else None

    match = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m?)?", text)
    if not match or not any(match.groups()):
        return None
    minutes = int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
    return minutes if 0 < minutes <= 24 * 60 else None


def parse_target_minutes(raw: str) -> int | None:
    """Parses a monthly target typed in HOURS: "20", "20h", "20.5".

    Hours, not minutes, because that is the unit a monthly target is decided
    in — reading "20" here as twenty minutes a month would be silently useless
    rather than obviously wrong.
    """
    text = raw.strip().lower().removesuffix("hours").removesuffix("hour").removesuffix("h").strip()
    try:
        hours = float(text)
    except ValueError:
        return None
    return round(hours * 60) if 0 < hours <= 31 * 24 else None


def fmt_minutes(minutes: int) -> str:
    """Compact durations, matching the worker's own formatting."""
    total = max(0, round(minutes))
    hours, rest = divmod(total, 60)
    if hours == 0:
        return f"{rest}m"
    if rest == 0:
        return f"{hours}h"
    return f"{hours}h{rest}m"


def describe_pace(pace: api_client.Pace) -> str:
    """Where a routine's month stands, leading with the gap.

    The gap first, because that is what decides whether today needs more time
    than usual; the running total is context.
    """
    spent = f"{fmt_minutes(pace['spentMinutes'])}/{fmt_minutes(pace['targetMinutes'])}"
    gap = fmt_minutes(abs(pace["deltaMinutes"]))
    if pace["status"] == "behind":
        return f"behind {gap} · {spent}"
    if pace["status"] == "ahead":
        return f"ahead {gap} · {spent}"
    return f"on track · {spent}"


def routines(now: api_client.NowTasks) -> list[api_client.TaskRow]:
    """The actively-pursued routines, which live in no schedule bucket."""
    return [task for task in (now.get("ongoing") or []) if task.get("pace")]


def top_level(tasks: list[api_client.TaskRow]) -> list[api_client.TaskRow]:
    return [task for task in tasks if not task.get("parentTaskId")]


def normalized_status(task: api_client.TaskRow) -> str:
    """Fold anything unexpected into "open" so it still gets rendered."""
    return task.get("status") if task.get("status") == STATUS_IN_PROGRESS else STATUS_OPEN


def all_tasks(now: api_client.NowTasks) -> list[api_client.TaskRow]:
    """Every top-level task the worker sent, de-duplicated, schedule-ordered.

    The buckets overlap by design: `nextUp` is `future[0]` restated for
    callers that group by schedule. Grouping by status means walking all of
    them, so dedupe by id or that task renders twice.

    Bucket order here *is* the sort order — overdue, then today, then future,
    then undated — which is why no explicit sort follows: the worker already
    orders each bucket (by dueAt, and unscheduled by createdAt).
    """
    ordered: list[api_client.TaskRow] = []
    seen: set[str] = set()
    for bucket in ("overdue", "today", "future", "unscheduled"):
        for task in top_level(now.get(bucket) or []):
            if task["id"] in seen:
                continue
            seen.add(task["id"])
            ordered.append(task)
    return ordered


def group_by_status(
    tasks: list[api_client.TaskRow],
) -> dict[str, list[api_client.TaskRow]]:
    grouped: dict[str, list[api_client.TaskRow]] = {STATUS_IN_PROGRESS: [], STATUS_OPEN: []}
    for task in tasks:
        grouped[normalized_status(task)].append(task)
    return grouped


def apply_completion(now: api_client.NowTasks, task_id: str) -> api_client.NowTasks:
    """Optimistic local update for a completed task.

    Two distinct cases: completing a *subtask* leaves its parent in place and
    only advances that parent's meter, while completing a top-level task makes
    the whole block leave the view. Getting this wrong is very visible —
    ticking a subtask used to look like nothing happened for up to 30s,
    because only top-level ids were ever matched.
    """

    def advance(task: api_client.TaskRow) -> api_client.TaskRow:
        step = task.get("nextStep")
        if not step or step["id"] != task_id:
            return task
        updated = dict(task)
        updated["nextStep"] = None
        progress = task.get("progress")
        if progress:
            updated["progress"] = {
                "done": min(progress["done"] + 1, progress["total"]),
                "total": progress["total"],
            }
        return updated  # type: ignore[return-value]

    owns_step = any(
        (task.get("nextStep") or {}).get("id") == task_id
        for task in [
            *now["overdue"],
            *now["today"],
            *(now.get("future") or []),
            *now["unscheduled"],
            now["nextUp"] or {},
        ]
    )
    if owns_step:
        updated_now = dict(now)
        for bucket in ("overdue", "today", "future", "unscheduled"):
            updated_now[bucket] = [advance(t) for t in (now.get(bucket) or [])]
        updated_now["nextUp"] = advance(now["nextUp"]) if now["nextUp"] else None
        return updated_now  # type: ignore[return-value]

    return remove_task_from_now(now, task_id)


def remove_task_from_now(now: api_client.NowTasks, task_id: str) -> api_client.NowTasks:
    """Optimistic local removal for a completed top-level task — it leaves
    wherever it currently sits well before the next poll confirms it."""
    updated = dict(now)
    for bucket in ("overdue", "today", "future"):
        updated[bucket] = [t for t in (now.get(bucket) or []) if t["id"] != task_id]
    updated["nextUp"] = None if now["nextUp"] and now["nextUp"]["id"] == task_id else now["nextUp"]
    was_unscheduled = any(t["id"] == task_id for t in now["unscheduled"])
    updated["unscheduled"] = [t for t in now["unscheduled"] if t["id"] != task_id]
    updated["unscheduledCount"] = now["unscheduledCount"] - 1 if was_unscheduled else now["unscheduledCount"]
    return updated  # type: ignore[return-value]


def apply_status_change(
    now: api_client.NowTasks, task_id: str, status: str
) -> api_client.NowTasks:
    """Optimistic local status flip — the task jumps sections immediately.

    Unlike completing, this is *not* a removal: the task keeps its due
    date and its place in the schedule ordering, only the section it lands in
    changes. So every bucket is rewritten in place rather than filtered, which
    also keeps the block's position stable within its new section.
    """
    def restatus(task: api_client.TaskRow) -> api_client.TaskRow:
        if task["id"] != task_id:
            return task
        updated = dict(task)
        updated["status"] = status
        return updated  # type: ignore[return-value]

    updated_now = dict(now)
    for bucket in ("overdue", "today", "future", "unscheduled"):
        updated_now[bucket] = [restatus(t) for t in (now.get(bucket) or [])]
    updated_now["nextUp"] = restatus(now["nextUp"]) if now["nextUp"] else None
    return updated_now  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Today Plan v1 — session-level model. A task can carry several sessions on
# the same day now, so nothing here groups by task; everything groups by
# session, and a task is only ever reached through the session that embeds it.


def active_sessions(today: api_client.Today | None) -> list[api_client.SessionRow]:
    """Every session worth showing at all — cancelled ones never render in
    the default daily view; they were a plan that changed, not a record."""
    if today is None:
        return []
    return [s for s in today["sessions"] if s["status"] != "cancelled"]


def planned_sessions(today: api_client.Today | None) -> list[api_client.SessionRow]:
    return sorted(
        (s for s in active_sessions(today) if s["status"] == "planned"),
        key=lambda s: s["position"],
    )


def resolved_sessions(today: api_client.Today | None) -> list[api_client.SessionRow]:
    """Done or skipped today — kept visible as a record, not a prompt."""
    return sorted(
        (s for s in active_sessions(today) if s["status"] in ("done", "skipped")),
        key=lambda s: s["position"],
    )


def today_task_ids(today: api_client.Today | None) -> set[str]:
    """Tasks that already have something planned or done today — used to
    keep Needs Attention from repeating what Today already covers."""
    return {s["taskId"] for s in active_sessions(today) if s["status"] in ("planned", "done")}


def today_total_minutes(today: api_client.Today | None) -> int:
    return sum(s["plannedMinutes"] for s in planned_sessions(today))


class EntryContext(NamedTuple):
    """Which inline field is open, if any.

    mode "plan" is the Today composer (focus text + duration chips) — adding
    a new item when session_id is None, revising one ("Replan") when it is
    set. mode "target" is the existing free-text monthly-target field.
    """

    task_id: str
    mode: str  # "plan" | "target"
    session_id: str | None = None


class TaskWidget:
    def __init__(self) -> None:
        self.window = WaylandWindow(
            title="persona-tasks-widget",
            layer="top",
            anchor="top right",
            margin="45px 10px 0px 0px",
            exclusivity="none",
            keyboard_mode="on-demand",
            visible=False,
        )
        self.window.set_size_request(WINDOW_WIDTH, -1)

        # Browse tasks is the backlog you go looking in, not the panel's main
        # event — it starts collapsed, unlike Today which is always shown.
        self.browse_expanded = False
        self._last_now: api_client.NowTasks | None = None
        # Which task's monthly-target picker is open, if any. One at a time:
        # designating a routine is a deliberate, occasional act, and letting
        # several pickers stand open would just make the panel taller.
        self._target_picker_for: str | None = None
        # The open Today composer / target field, if any.
        self._entry_for: EntryContext | None = None
        # True while the open composer's duration chips have been swapped for
        # a free-text field ("…") — for any length not on the fixed list.
        self._composer_custom = False
        # Today's sessions, or None while the sessions half has never been
        # fetched successfully — the panel still draws without it.
        self._last_today: api_client.Today | None = None
        self._missed_expanded = False
        self._last_sync: datetime | None = None
        self._failure_streak = 0
        # Set when a write (start/stop, complete, plan) is rejected. Those
        # all render optimistically, so without this the task simply snaps
        # back at the next poll and the widget looks broken rather than
        # honest about having failed.
        self._write_error: str | None = None
        self.footer_label: Label | None = None

        self.header_title = Label(label="TODAY", style_classes="header", h_align="start", h_expand=True)
        close_btn = Button(label="×", style_classes="close-btn")
        close_btn.connect("clicked", lambda _btn: self.hide())
        header_row = Box(orientation="h", spacing=8, style_classes="header-row content-inset")
        header_row.add(self.header_title)
        header_row.add(close_btn)

        self.body = Box(orientation="v", spacing=4, style_classes="content-inset")
        self.scroller = ScrolledWindow(
            child=self.body,
            h_scrollbar_policy="never",
            v_scrollbar_policy="automatic",
            max_content_size=(-1, MAX_BODY_HEIGHT),
            propagate_width=True,
            propagate_height=True,
            overlay_scroll=True,
            style_classes="body-scroll",
        )

        outer = Box(orientation="v", spacing=0)
        outer.add(header_row)
        outer.add(self.scroller)
        self.window.add(outer)

        self.render_loading()
        self.window.show_all()

        GLib.timeout_add_seconds(POLL_INTERVAL_SECONDS, self._poll)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._toggle_visibility)
        self._poll()

    def _sync_body_height(self) -> bool:
        """Pin the scroller's minimum height to what the body actually needs.

        gtk-layer-shell sizes a non-stretched surface from the window's
        *minimum* height, and a ScrolledWindow's minimum is near zero because
        scrolling is always an option — left alone, that collapsed the whole
        widget to a 68px sliver regardless of how many tasks were in it.
        Setting min-content-height to the body's natural height (capped, so
        long lists scroll instead of running off screen) makes minimum and
        natural agree again.
        """
        _, natural = self.body.get_preferred_height()
        self.scroller.set_min_content_height(min(natural, MAX_BODY_HEIGHT))
        return False

    def hide(self) -> None:
        self.window.hide()

    def show(self) -> None:
        self.window.show_all()
        # Re-poll on every reveal: after a long hidden stretch the rendered
        # state (and its "synced Nm ago") is as stale as the hide was long.
        self._poll()

    def _toggle_visibility(self) -> bool:
        if self.window.get_visible():
            self.hide()
        else:
            self.show()
        return True

    def _clear(self) -> None:
        self.footer_label = None
        for child in self.body.get_children():
            self.body.remove(child)

    def render_loading(self) -> None:
        self._clear()
        self.header_title.set_label("TODAY")
        self.body.add(Label(label="Loading…", style_classes="state-message", h_align="start"))
        self.body.show_all()
        GLib.idle_add(self._sync_body_height)

    def render_message(self, message: str, error: bool = False) -> None:
        self._clear()
        self.header_title.set_label("TODAY")
        classes = "state-message error" if error else "state-message"
        self.body.add(Label(label=message, style_classes=classes, h_align="start"))
        self.body.show_all()
        GLib.idle_add(self._sync_body_height)

    def _run_async(self, work, on_done) -> None:
        # Every api_client call is a blocking HTTP request — running it
        # directly on the GTK thread freezes the whole mainloop (no repaint,
        # no input) until it returns, which on Wayland reads as the widget
        # vanishing until the compositor gets a frame from it again. Do the
        # network call on a worker thread; only touch widgets back on the
        # GTK thread via GLib.idle_add.
        def runner() -> None:
            try:
                result, error = work(), None
            except Exception as exc:  # noqa: BLE001 - surfaced to on_done, not swallowed
                result, error = None, exc

            def deliver() -> bool:
                on_done(result, error)
                return False

            GLib.idle_add(deliver)

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> bool:
        self._run_async(api_client.fetch_panel, self._handle_poll_result)
        return True

    def _handle_poll_result(self, panel, error) -> None:
        if isinstance(error, api_client.NoDesktopTokenError):
            self.render_message("Run persona-connect to link this desktop.")
        elif isinstance(error, api_client.DesktopTokenRevokedError):
            self.render_message("Token revoked — run persona-connect again.", error=True)
        elif error is not None:
            # Transient network/worker hiccup (including Render cold-start) —
            # keep whatever's currently rendered rather than flashing an error
            # every 30s. The footer is the only thing that changes, so a stale
            # list is never mistaken for an up-to-date empty one.
            self._failure_streak += 1
            self._refresh_footer()
        else:
            self._failure_streak = 0
            self._last_sync = datetime.now(timezone.utc)
            self._last_today = panel["today"]
            # Re-rendering rebuilds every widget, which would destroy an open
            # entry mid-keystroke. The poll's data is kept; the view catches
            # up as soon as the field closes.
            if self._entry_for is None:
                self.render(panel["now"])
            else:
                self._last_now = panel["now"]

    # -- render ------------------------------------------------------------

    def render(self, now: api_client.NowTasks) -> None:
        self._last_now = now
        self._clear()

        today = self._last_today
        grouped = group_by_status(all_tasks(now))
        in_progress = grouped[STATUS_IN_PROGRESS]
        open_tasks = grouped[STATUS_OPEN]

        if today is not None:
            self._render_today_first(now, today)
        else:
            # The sessions half never landed — fall back to the plain task
            # list rather than blanking a section the panel can't fill in.
            self.header_title.set_label(f"TASKS · {len(in_progress) + len(open_tasks)}")
            self.body.add(
                Label(
                    label="Today plan unavailable",
                    style_classes="state-message",
                    h_align="start",
                )
            )
            self._render_browse(now, in_progress, open_tasks, force_expanded=True)

        self.footer_label = Label(label="", style_classes="sync-note", h_align="start")
        self.body.add(self.footer_label)
        self._refresh_footer()

        self.body.show_all()
        # Deferred: preferred-height only reflects the new children after the
        # pending resize has been processed.
        GLib.idle_add(self._sync_body_height)

    def _render_today_first(self, now: api_client.NowTasks, today: api_client.Today) -> None:
        planned = planned_sessions(today)
        resolved = resolved_sessions(today)
        total_minutes = today_total_minutes(today)
        count = len(planned) + len(resolved)

        self.header_title.set_label(
            f"TODAY · {fmt_minutes(total_minutes)} · {count}" if count else "TODAY"
        )

        if today["missedYesterday"]:
            self.body.add(self._build_missed_banner(today["missedYesterday"]))

        if planned:
            self.body.add(self._section_label("NEXT"))
            self.body.add(self._build_today_item(planned[0], primary=True))

            if len(planned) > 1:
                self.body.add(self._section_label("LATER"))
                for session in planned[1:]:
                    self.body.add(self._build_today_item(session, primary=False))
        elif not resolved:
            self.body.add(self._build_empty_today_state())

        if resolved:
            self.body.add(self._section_label("DONE"))
            for session in resolved:
                self.body.add(self._build_resolved_row(session))

        attention_routines, attention_tasks = self._attention_items(now, today)
        if attention_routines or attention_tasks:
            self.body.add(self._section_label("NEEDS ATTENTION"))
            for task in attention_routines:
                self.body.add(self._build_attention_routine_row(task))
            for task in attention_tasks:
                self.body.add(self._build_attention_task_row(task))

        grouped = group_by_status(all_tasks(now))
        self._render_browse(now, grouped[STATUS_IN_PROGRESS], grouped[STATUS_OPEN])

    def _attention_items(
        self, now: api_client.NowTasks, today: api_client.Today
    ) -> tuple[list[api_client.TaskRow], list[api_client.TaskRow]]:
        """Routines slipping behind, and due/overdue tasks, that have nothing
        planned or done for today yet — the reason to open the composer."""
        covered = today_task_ids(today)

        # Any active routine not yet planned or done today is a candidate for
        # Needs Attention — "unplanned" is the trigger; being behind on the
        # month is what the row's own text then makes urgent-looking.
        attention_routines = [task for task in routines(now) if task["id"] not in covered]

        due_candidates: list[api_client.TaskRow] = []
        seen: set[str] = set()
        for bucket in ("overdue", "today"):
            for task in top_level(now.get(bucket) or []):
                if task["id"] in covered or task["id"] in seen or task.get("pace"):
                    continue
                seen.add(task["id"])
                due_candidates.append(task)
        attention_tasks = due_candidates[:MAX_ATTENTION_TASKS]

        return attention_routines, attention_tasks

    def _render_browse(
        self,
        now: api_client.NowTasks,
        in_progress: list[api_client.TaskRow],
        open_tasks: list[api_client.TaskRow],
        force_expanded: bool = False,
    ) -> None:
        expanded = force_expanded or self.browse_expanded
        total = len(in_progress) + len(open_tasks)

        if not force_expanded:
            toggle = Button(
                child=self._section_header_content("Browse tasks", total, 0, arrow=expanded),
                style_classes="section-title section-toggle browse",
            )
            toggle.connect("clicked", self._toggle_browse_expanded)
            self.body.add(toggle)

        if not expanded:
            return

        if total == 0:
            self.body.add(
                Label(label="Nothing else open", style_classes="footer-note", h_align="start")
            )
            return

        shown = 0
        for status, title, tone in STATUS_SECTIONS:
            tasks = in_progress if status == STATUS_IN_PROGRESS else open_tasks
            if not tasks:
                continue
            overdue_count = sum(1 for t in tasks if t.get("dueAt") and is_overdue(t["dueAt"]))
            self.body.add(self._build_section_title(title, tone, len(tasks), overdue_count))
            for task in tasks:
                if shown >= MAX_TASKS_SHOWN:
                    break
                self.body.add(self._build_browse_task_block(task))
                shown += 1

        hidden_by_worker = max(0, now["unscheduledCount"] - len(top_level(now["unscheduled"])))
        hidden = max(0, total - shown) + hidden_by_worker
        if hidden > 0:
            self.body.add(
                Label(label=f"+{hidden} more not shown", style_classes="footer-note", h_align="start")
            )

    def _toggle_browse_expanded(self, *_args) -> None:
        self.browse_expanded = not self.browse_expanded
        if self._last_now is not None:
            self.render(self._last_now)

    def _refresh_footer(self) -> None:
        if self.footer_label is None:
            return
        if self._write_error is not None:
            self.footer_label.set_label(self._write_error)
            self.footer_label.add_style_class("stale")
        elif self._failure_streak > 0:
            times = "" if self._failure_streak == 1 else f" {self._failure_streak}×"
            age = f" · {relative_sync_age(self._last_sync)}" if self._last_sync else ""
            self.footer_label.set_label(f"sync failed{times}{age}")
            self.footer_label.add_style_class("stale")
        else:
            self.footer_label.remove_style_class("stale")
            self.footer_label.set_label(
                relative_sync_age(self._last_sync) if self._last_sync else ""
            )

    def _build_empty_today_state(self) -> Box:
        row = Box(orientation="h", spacing=10, style_classes="empty-state")
        row.add(Label(label="✓", style_classes="empty-check", v_align="start"))
        text = Box(orientation="v", spacing=1)
        text.add(Label(label="Nothing planned yet", style_classes="empty-title", h_align="start"))
        text.add(
            Label(
                label="Add something from Browse tasks or Needs attention",
                style_classes="empty-subtitle",
                h_align="start",
            )
        )
        row.add(text)
        return row

    def _section_label(self, title: str) -> Label:
        return Label(label=title, style_classes="section-label today-label", h_align="start")

    def _section_header_content(
        self, title: str, count: int | None, overdue_count: int, arrow: bool | None = None
    ) -> Box:
        label = f"{title} · {count}" if count is not None else title
        content = Box(orientation="h", spacing=6)
        content.add(
            Label(
                label=label,
                style_classes="section-label",
                h_align="start",
                h_expand=True,
            )
        )
        if overdue_count:
            content.add(
                Label(label=f"{overdue_count} overdue", style_classes="section-overdue-badge")
            )
        if arrow is not None:
            content.add(Label(label="▴" if arrow else "▾", style_classes="section-arrow"))
        return content

    def _build_section_title(self, title: str, tone: str, count: int, overdue_count: int) -> Box:
        row = Box(orientation="h", style_classes=f"section-title {tone}")
        row.add(self._section_header_content(title, count, overdue_count))
        return row

    # -- Today items ---------------------------------------------------

    def _session_by_id(self, session_id: str) -> api_client.SessionRow | None:
        if self._last_today is None:
            return None
        for session in self._last_today["sessions"]:
            if session["id"] == session_id:
                return session
        return None

    def _build_today_item(self, session: api_client.SessionRow, primary: bool) -> Box:
        task = session["task"]
        focus_text = session.get("focusText") or clamp_title(task["title"])
        ticket_key, bare_title = split_ticket_key(task["title"])
        parent_label = ticket_key or bare_title

        tone = priority_class(task["priority"], "accent")
        row = Box(
            orientation="v",
            spacing=1,
            style_classes=f"today-item {tone}" + (" primary" if primary else " compact"),
        )

        head = Box(orientation="h", spacing=6)
        head.add(
            Label(
                label=focus_text,
                style_classes="task-title" if primary else "step-title",
                h_align="start",
                h_expand=True,
                ellipsization="end",
            )
        )
        head.add(self._build_session_complete_button(session))
        head.add(self._build_session_skip_button(session))
        head.add(self._build_session_overflow_button(session))
        row.add(head)

        meta_bits = [parent_label, fmt_minutes(session["plannedMinutes"])]
        if session.get("startAt"):
            meta_bits.append(format_time_of_day(session["startAt"]))
        row.add(
            Label(
                label=" · ".join(meta_bits),
                style_classes="task-meta",
                h_align="start",
            )
        )

        if self._entry_for == EntryContext(task["id"], "plan", session["id"]):
            row.add(self._build_composer(task, EntryContext(task["id"], "plan", session["id"])))

        return row

    def _build_resolved_row(self, session: api_client.SessionRow) -> Box:
        task = session["task"]
        focus_text = session.get("focusText") or clamp_title(task["title"])
        if session["status"] == "done":
            actual = session["actualMinutes"] or session["plannedMinutes"]
            text = f"{focus_text} · {fmt_minutes(actual)} done"
        else:
            text = f"{focus_text} · skipped"
        return Label(
            label=text,
            style_classes="step-title done" if session["status"] == "done" else "step-title",
            h_align="start",
        )

    def _build_session_complete_button(self, session: api_client.SessionRow) -> Button:
        btn = Button(label="✓", style_classes="complete-btn today-action", tooltip_text="Done")
        btn.connect(
            "clicked", lambda _btn, sid=session["id"]: self._on_complete_session(sid)
        )
        return btn

    def _build_session_skip_button(self, session: api_client.SessionRow) -> Button:
        btn = Button(label="⊘", style_classes="plan-btn today-action", tooltip_text="Skip today")
        btn.connect("clicked", lambda _btn, sid=session["id"]: self._on_skip_session(sid))
        return btn

    def _build_session_overflow_button(self, session: api_client.SessionRow) -> Button:
        btn = Button(label="···", style_classes="plan-btn today-action", tooltip_text="More")
        btn.connect(
            "clicked",
            lambda _btn, s=session: self._popup_menu(
                btn,
                [
                    ("Replan", lambda s=s: self._open_replan(s)),
                    ("Remove", lambda s=s: self._on_cancel_session(s["id"])),
                ],
            ),
        )
        return btn

    def _open_replan(self, session: api_client.SessionRow) -> None:
        self._entry_for = EntryContext(session["taskId"], "plan", session["id"])
        self._composer_custom = False
        if self._last_now is not None:
            self.render(self._last_now)

    # -- Needs attention -------------------------------------------------

    def _build_attention_routine_row(self, task: api_client.TaskRow) -> Box:
        pace = task["pace"]
        row = Box(orientation="h", spacing=6, style_classes="attention-row")
        text = Box(orientation="v", spacing=0)
        text.add(Label(label=clamp_title(task["title"]), style_classes="task-title", h_align="start"))
        text.add(
            Label(
                label=describe_pace(pace),
                style_classes="task-meta" + (" overdue" if pace["status"] == "behind" else ""),
                h_align="start",
            )
        )
        row.add(text)
        suggested = pace["suggestedTodayMinutes"] or PLAN_OPTIONS[0][1]
        chip = Button(
            label=f"+{fmt_minutes(suggested)}",
            style_classes="plan-btn current",
            tooltip_text=f"Plan {fmt_minutes(suggested)} today",
        )
        chip.connect(
            "clicked", lambda _btn, tid=task["id"], m=suggested: self._on_plan(tid, m)
        )
        row.add(chip)
        miss = Button(
            label="⊘",
            style_classes="plan-btn",
            tooltip_text="Not today — recorded as a miss, not held against the month",
        )
        miss.connect(
            "clicked", lambda _btn, tid=task["id"], m=suggested: self._on_miss(tid, m)
        )
        row.add(miss)
        return row

    def _build_attention_task_row(self, task: api_client.TaskRow) -> Box:
        ticket_key, bare_title = split_ticket_key(task["title"])
        due_at = task.get("dueAt")
        row = Box(orientation="h", spacing=6, style_classes="attention-row")
        text = Box(orientation="v", spacing=0)
        meta_bits = [b for b in (ticket_key,) if b]
        if due_at:
            meta_bits.append(relative_deadline(due_at))
        if meta_bits:
            text.add(
                Label(
                    label=" · ".join(meta_bits),
                    style_classes="task-meta" + (" overdue" if due_at and is_overdue(due_at) else ""),
                    h_align="start",
                )
            )
        text.add(Label(label=clamp_title(bare_title), style_classes="task-title", h_align="start"))
        row.add(text)

        if self._entry_for == EntryContext(task["id"], "plan", None):
            row.add(self._build_composer(task, EntryContext(task["id"], "plan", None)))
        else:
            add_btn = Button(label="+", style_classes="plan-btn today-action", tooltip_text="Add to Today")
            add_btn.connect("clicked", lambda _btn, tid=task["id"]: self._open_composer(tid))
            row.add(add_btn)
        return row

    def _build_missed_banner(self, missed: list[api_client.SessionRow]) -> Box:
        outer = Box(orientation="v", spacing=4, style_classes="missed-banner")
        toggle = Button(
            child=self._section_header_content(
                f"{len(missed)} unfinished from yesterday", None, 0, arrow=self._missed_expanded
            ),
            style_classes="section-title section-toggle missed",
        )
        toggle.connect("clicked", self._toggle_missed_expanded)
        outer.add(toggle)

        if self._missed_expanded:
            for session in missed:
                outer.add(self._build_missed_row(session))
        return outer

    def _toggle_missed_expanded(self, *_args) -> None:
        self._missed_expanded = not self._missed_expanded
        if self._last_now is not None:
            self.render(self._last_now)

    def _build_missed_row(self, session: api_client.SessionRow) -> Box:
        task = session["task"]
        focus_text = session.get("focusText") or clamp_title(task["title"])
        row = Box(orientation="h", spacing=6, style_classes="attention-row")
        row.add(
            Label(
                label=f"{focus_text} · {fmt_minutes(session['plannedMinutes'])}",
                style_classes="task-meta",
                h_align="start",
                h_expand=True,
            )
        )
        add_btn = Button(label="+ today", style_classes="plan-btn", tooltip_text="Carry forward to today")
        add_btn.connect(
            "clicked",
            lambda _btn, s=session: self._on_carry_forward(s),
        )
        row.add(add_btn)
        skip_btn = Button(label="⊘", style_classes="plan-btn", tooltip_text="Leave it — mark yesterday as skipped")
        skip_btn.connect("clicked", lambda _btn, sid=session["id"]: self._on_skip_session(sid))
        row.add(skip_btn)
        return row

    # -- composer ---------------------------------------------------------

    def _open_composer(self, task_id: str) -> None:
        self._entry_for = EntryContext(task_id, "plan", None)
        self._composer_custom = False
        if self._last_now is not None:
            self.render(self._last_now)

    def _close_entry(self) -> None:
        self._entry_for = None
        self._composer_custom = False
        if self._last_now is not None:
            self.render(self._last_now)

    def _build_composer(self, task: api_client.TaskRow, ctx: EntryContext) -> Box:
        """Focus text plus duration chips. Clicking a chip commits
        immediately with whatever's in the focus field — there's no separate
        "Add" step, matching how every other chip in this panel works.
        Editing the item's time of day isn't wired up yet (view-only on the
        row above); the worker has no reorder endpoint either, so the item
        lands wherever `position` puts it."""
        session = self._session_by_id(ctx.session_id) if ctx.session_id else None
        initial_focus = (session.get("focusText") if session else None) or ""

        box = Box(orientation="v", spacing=4, style_classes="composer")
        entry = Entry(
            text=initial_focus,
            placeholder=clamp_title(split_ticket_key(task["title"])[1]),
            style_classes="inline-entry",
            h_expand=True,
        )

        def on_key(_widget, event) -> bool:
            if event.keyval == Gdk.KEY_Escape:
                self._close_entry()
                return True
            return False

        entry.connect("key-press-event", on_key)
        box.add(entry)

        if self._composer_custom:
            box.add(self._build_custom_duration_row(task, ctx, entry))
        else:
            chips = Box(orientation="h", spacing=6)
            options = list(PLAN_OPTIONS)
            if session and session["plannedMinutes"] not in [m for _, m in options]:
                options.insert(0, (fmt_minutes(session["plannedMinutes"]), session["plannedMinutes"]))
            for label_text, minutes in options:
                chip = Button(label=label_text, style_classes="plan-btn", tooltip_text=f"{fmt_minutes(minutes)}")
                chip.connect(
                    "clicked",
                    lambda _btn, m=minutes: self._commit_composer(task["id"], ctx, entry.get_text(), m),
                )
                chips.add(chip)
            more = Button(label="…", style_classes="plan-btn", tooltip_text="Type any length — 90, 1h30, 45m")
            more.connect("clicked", lambda _btn: self._open_custom_duration())
            chips.add(more)
            cancel = Button(label="✕", style_classes="plan-btn", tooltip_text="Cancel (Esc)")
            cancel.connect("clicked", lambda _btn: self._close_entry())
            chips.add(cancel)
            box.add(chips)
            GLib.idle_add(entry.grab_focus)
        return box

    def _open_custom_duration(self) -> None:
        self._composer_custom = True
        if self._last_now is not None:
            self.render(self._last_now)

    def _build_custom_duration_row(
        self, task: api_client.TaskRow, ctx: EntryContext, focus_entry: Entry
    ) -> Box:
        row = Box(orientation="h", spacing=6)
        duration_entry = Entry(
            placeholder="90 or 1h30", style_classes="inline-entry", h_expand=True
        )

        def commit(_widget=None) -> None:
            minutes = parse_duration_minutes(duration_entry.get_text())
            if minutes is None:
                duration_entry.add_style_class("invalid")
                return
            self._commit_composer(task["id"], ctx, focus_entry.get_text(), minutes)

        def on_key(_widget, event) -> bool:
            if event.keyval == Gdk.KEY_Escape:
                self._close_entry()
                return True
            duration_entry.remove_style_class("invalid")
            return False

        duration_entry.connect("activate", commit)
        duration_entry.connect("key-press-event", on_key)
        row.add(duration_entry)
        ok = Button(label="✓", style_classes="plan-btn current", tooltip_text="Set")
        ok.connect("clicked", lambda _btn: commit())
        row.add(ok)
        cancel = Button(label="✕", style_classes="plan-btn", tooltip_text="Cancel (Esc)")
        cancel.connect("clicked", lambda _btn: self._close_entry())
        row.add(cancel)
        GLib.idle_add(duration_entry.grab_focus)
        return row

    def _commit_composer(self, task_id: str, ctx: EntryContext, focus_text: str, minutes: int) -> None:
        text = focus_text.strip() or None
        self._entry_for = None
        self._composer_custom = False
        if ctx.session_id is not None:
            self._write(
                lambda: api_client.plan_session(
                    task_id, minutes, session_id=ctx.session_id, focus_text=text
                ),
                "Replan",
            )
        else:
            self._write(
                lambda: api_client.plan_session(task_id, minutes, focus_text=text), "Add"
            )

    # -- Browse tasks -----------------------------------------------------

    def _build_meter(self, progress: api_client.Progress) -> Box:
        """Subtask roll-up. Pips stay countable up to PIP_MAX_TOTAL; past that
        a proportional bar carries the ratio better than a row of dots. The
        done/total fraction is always spelled out beside it. """
        done = max(0, min(progress["done"], progress["total"]))
        total = progress["total"]
        meter = Box(orientation="h", spacing=6, style_classes="meter", v_align="center")

        if total <= PIP_MAX_TOTAL:
            pips = Box(orientation="h", spacing=0)
            if done:
                pips.add(Label(label="●" * done, style_classes="pips-done"))
            if total - done:
                pips.add(Label(label="○" * (total - done), style_classes="pips-todo"))
            meter.add(pips)
        else:
            track = Box(orientation="h", style_classes="meter-track", v_align="center")
            track.set_size_request(METER_BAR_WIDTH, -1)
            fill = Box(style_classes="meter-fill")
            fill.set_size_request(max(2, round(METER_BAR_WIDTH * done / total)), -1)
            track.add(fill)
            meter.add(track)

        complete = done >= total
        classes = "meter-count complete" if complete else "meter-count"
        meter.add(Label(label=f"{done}/{total}", style_classes=classes))
        return meter

    def _build_complete_button(
        self, task: api_client.TaskRow, progress: api_client.Progress | None
    ) -> Button:
        remaining = progress["total"] - progress["done"] if progress else 0
        if remaining > 0:
            plural = "" if remaining == 1 else "s"
            btn = Button(
                label="✓",
                style_classes="complete-btn blocked",
                tooltip_text=f"{remaining} open subtask{plural} — complete those first",
            )
            btn.set_sensitive(False)
            return btn

        btn = Button(label="✓", style_classes="complete-btn", tooltip_text="Mark complete")
        btn.connect("clicked", lambda _btn, task_id=task["id"]: self._on_complete(task_id))
        return btn

    def _build_status_button(self, task: api_client.TaskRow) -> Button:
        if normalized_status(task) == STATUS_IN_PROGRESS:
            btn = Button(
                label="⏸",
                style_classes="status-btn started",
                tooltip_text="Stop — move back to Open",
            )
            target = STATUS_OPEN
        else:
            btn = Button(
                label="▶",
                style_classes="status-btn",
                tooltip_text="Start — move to In progress",
            )
            target = STATUS_IN_PROGRESS

        btn.connect(
            "clicked",
            lambda _btn, task_id=task["id"], status=target: self._on_set_status(task_id, status),
        )
        return btn

    def _build_browse_task_block(self, task: api_client.TaskRow) -> EventBox:
        """Browse's version of a task block — identity, subtask meter, next
        step, and "+ Today" to plan it, with routine/overflow actions tucked
        behind ···. No duration chips here any more: deciding today's minutes
        happens in the Today composer, not on every backlog row."""
        progress = task.get("progress")
        if progress and progress["total"] <= 0:
            progress = None
        ticket_key, bare_title = split_ticket_key(task["title"])
        due_at = task.get("dueAt")

        status_class = (
            "status-in-progress"
            if normalized_status(task) == STATUS_IN_PROGRESS
            else "status-open"
        )
        block = Box(
            orientation="v",
            spacing=2,
            style_classes=f"task-block {priority_class(task['priority'], 'accent')} {status_class}",
        )

        head = Box(orientation="h", spacing=6, style_classes="task-head")
        if ticket_key:
            head.add(Label(label=ticket_key, style_classes="ticket-badge"))
        head.add(
            Label(
                label=task.get("type") or "",
                style_classes="type-label",
                h_align="start",
                h_expand=True,
                ellipsization="end",
            )
        )
        if due_at:
            head.add(
                Label(
                    label=relative_deadline(due_at),
                    style_classes="task-meta" + (" overdue" if is_overdue(due_at) else ""),
                )
            )
        if progress:
            head.add(self._build_meter(progress))
        block.add(head)

        title_row = Box(orientation="h", spacing=6)
        title_row.add(
            Label(
                label=clamp_title(bare_title),
                style_classes="task-title",
                h_align="start",
                h_expand=True,
                justification="left",
                line_wrap="word-char",
                size=(TITLE_WIDTH, -1),
            )
        )
        title_row.add(self._build_status_button(task))
        add_btn = Button(label="+", style_classes="status-btn", tooltip_text="Add to Today")
        add_btn.connect("clicked", lambda _btn, tid=task["id"]: self._open_composer(tid))
        title_row.add(add_btn)
        title_row.add(self._build_browse_overflow_button(task, progress))
        block.add(title_row)

        if self._entry_for == EntryContext(task["id"], "plan", None):
            block.add(self._build_composer(task, EntryContext(task["id"], "plan", None)))

        if self._target_picker_for == task["id"]:
            block.add(
                self._build_target_entry(task)
                if self._entry_for == EntryContext(task["id"], "target")
                else self._build_target_picker(task)
            )

        next_step = task.get("nextStep")
        if next_step:
            step_row = Box(orientation="h", spacing=6, style_classes="next-step-row")
            step_row.add(Label(label="└", style_classes="step-connector", v_align="start"))
            label = f"next: {next_step['title']}"
            if progress:
                label += f" · {progress['done']}/{progress['total']}"
            step_row.add(
                Label(
                    label=label,
                    style_classes="step-title",
                    h_align="start",
                    h_expand=True,
                    ellipsization="end",
                )
            )
            step_row.add(self._build_complete_button(next_step, None))
            block.add(step_row)

        hover_box = EventBox(events=["enter-notify", "leave-notify"], child=block)
        hover_box.connect(
            "enter-notify-event", lambda _w, event: self._set_block_hovered(event, block, True)
        )
        hover_box.connect(
            "leave-notify-event", lambda _w, event: self._set_block_hovered(event, block, False)
        )
        return hover_box

    def _build_browse_overflow_button(
        self, task: api_client.TaskRow, progress: api_client.Progress | None
    ) -> Button:
        is_routine = task.get("pace") is not None
        remaining = progress["total"] - progress["done"] if progress else 0
        items = []
        if remaining <= 0:
            items.append(("Complete", lambda tid=task["id"]: self._on_complete(tid)))
        items.append(
            (
                "Change monthly target" if is_routine else "Make routine",
                lambda tid=task["id"]: self._toggle_target_picker(tid),
            )
        )
        if is_routine:
            items.append(("Stop measuring monthly", lambda tid=task["id"]: self._on_set_routine(tid, None)))

        btn = Button(label="···", style_classes="status-btn", tooltip_text="More")
        btn.connect("clicked", lambda _btn: self._popup_menu(btn, items))
        return btn

    def _popup_menu(self, button: Gtk.Widget, items: list[tuple[str, "callable"]]) -> None:
        menu = Gtk.Menu()
        menu.get_style_context().add_class("panel-menu")
        for label, callback in items:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None)

    def _toggle_target_picker(self, task_id: str) -> None:
        self._target_picker_for = None if self._target_picker_for == task_id else task_id
        if self._last_now is not None:
            self.render(self._last_now)

    def _build_more_target_chip(self, task_id: str) -> Button:
        chip = Button(
            label="⋯",
            style_classes="plan-btn",
            tooltip_text="Type any number of hours per month",
        )
        chip.connect(
            "clicked",
            lambda _btn, t=task_id: self._open_target_entry(t),
        )
        return chip

    def _open_target_entry(self, task_id: str) -> None:
        self._entry_for = EntryContext(task_id, "target")
        if self._last_now is not None:
            self.render(self._last_now)

    def _build_target_picker(self, task: api_client.TaskRow) -> Box:
        """Hours-per-month chips, plus a way back out for an existing routine."""
        pace = task.get("pace")
        row = Box(orientation="h", spacing=6, style_classes="target-picker")
        row.add(
            Label(
                label="per month",
                style_classes="target-picker-label",
                h_align="start",
                h_expand=True,
            )
        )
        for label_text, minutes in MONTHLY_TARGET_OPTIONS:
            current = pace is not None and pace["targetMinutes"] == minutes
            chip = Button(
                label=label_text,
                style_classes="plan-btn current" if current else "plan-btn",
                tooltip_text=f"Measure this at {label_text} a month",
            )
            chip.connect(
                "clicked",
                lambda _btn, task_id=task["id"], m=minutes: self._on_set_routine(task_id, m),
            )
            row.add(chip)
        row.add(self._build_more_target_chip(task["id"]))
        if pace is not None:
            stop = Button(
                label="✕",
                style_classes="plan-btn",
                tooltip_text="Stop measuring monthly (the task itself keeps going)",
            )
            stop.connect(
                "clicked",
                lambda _btn, task_id=task["id"]: self._on_set_routine(task_id, None),
            )
            row.add(stop)
        return row

    def _build_target_entry(self, task: api_client.TaskRow) -> Box:
        """A one-line field replacing the target chips, committed with Enter.

        Escape closes it. So does a value that will not parse, except that
        the field stays open and marked instead — losing what was typed
        because a stray character slipped in would be worse than the typo.
        """
        pace = task.get("pace")
        initial = f"{pace['targetMinutes'] / 60:g}" if pace else ""

        row = Box(orientation="h", spacing=6, style_classes="inline-entry-row")
        row.add(
            Label(label="hours/month", style_classes="target-picker-label", h_align="start")
        )
        entry = Entry(
            text=initial,
            placeholder="20",
            style_classes="inline-entry",
            h_expand=True,
        )

        def commit(_widget=None) -> None:
            minutes = parse_target_minutes(entry.get_text())
            if minutes is None:
                entry.add_style_class("invalid")
                return
            self._entry_for = None
            self._target_picker_for = None
            self._on_set_routine(task["id"], minutes)

        def on_key(_widget, event) -> bool:
            if event.keyval == Gdk.KEY_Escape:
                self._close_entry()
                return True
            entry.remove_style_class("invalid")
            return False

        entry.connect("activate", commit)
        entry.connect("key-press-event", on_key)
        row.add(entry)

        ok = Button(label="✓", style_classes="plan-btn current", tooltip_text="Set")
        ok.connect("clicked", lambda _btn: commit())
        row.add(ok)
        cancel = Button(label="✕", style_classes="plan-btn", tooltip_text="Cancel (Esc)")
        cancel.connect("clicked", lambda _btn: self._close_entry())
        row.add(cancel)

        GLib.idle_add(entry.grab_focus)
        return row

    def _set_block_hovered(self, event, block: Box, hovered: bool) -> bool:
        # Crossing into a child (a chip, the ✓) fires leave-notify with
        # detail=INFERIOR on the EventBox itself; acting on it would flicker
        # the block the moment you moved toward a button.
        if event is not None and event.detail == Gdk.NotifyType.INFERIOR:
            return False

        if hovered:
            block.add_style_class("hovered")
        else:
            block.remove_style_class("hovered")
        return False

    # -- writes -------------------------------------------------------------

    def _write(self, work, failure_label: str) -> None:
        """Run a write, then re-poll to replace the optimistic render with the
        worker's actual state. A rejection is reported in the footer instead
        of being swallowed, because the optimistic row has already reverted by
        the time the poll lands."""

        def done(_result, error) -> None:
            if error is None:
                self._write_error = None
            elif isinstance(error, api_client.NoDesktopTokenError):
                self._write_error = f"{failure_label} failed — run persona-connect"
            elif isinstance(error, api_client.DesktopTokenRevokedError):
                self._write_error = f"{failure_label} failed — token revoked"
            else:
                self._write_error = f"{failure_label} failed — {error}"
            self._refresh_footer()
            self._poll()

        self._run_async(work, done)

    def _on_complete(self, task_id: str) -> None:
        if self._last_now is not None:
            self.render(apply_completion(self._last_now, task_id))
        self._write(lambda: api_client.complete_task(task_id), "Complete")

    def _on_set_status(self, task_id: str, status: str) -> None:
        if self._last_now is not None:
            self.render(apply_status_change(self._last_now, task_id, status))
        label = "Start" if status == STATUS_IN_PROGRESS else "Stop"
        self._write(lambda: api_client.set_task_status(task_id, status), label)

    # No optimistic render for the session writes below. Every one of them
    # can change a routine's pace, and suggestedTodayMinutes depends on
    # arithmetic over the whole month that this panel deliberately does not
    # reimplement — a guessed number would be shown and then visibly jump when
    # the poll landed. _write re-polls on success, so the correct numbers
    # arrive a moment later instead.
    def _on_set_routine(self, task_id: str, minutes: int | None) -> None:
        self._target_picker_for = None
        label = "Make routine" if minutes is not None else "Stop measuring"
        self._write(lambda: api_client.set_routine_target(task_id, minutes), label)

    def _on_plan(self, task_id: str, minutes: int) -> None:
        self._write(lambda: api_client.plan_session(task_id, minutes), "Plan")

    def _on_complete_session(self, session_id: str) -> None:
        self._write(lambda: api_client.complete_session(session_id), "Done")

    def _on_skip_session(self, session_id: str) -> None:
        self._write(lambda: api_client.skip_session(session_id), "Skip")

    def _on_cancel_session(self, session_id: str) -> None:
        self._write(lambda: api_client.cancel_session(session_id), "Remove")

    def _on_miss(self, task_id: str, minutes: int) -> None:
        self._write(lambda: api_client.mark_missed(task_id, minutes), "Miss")

    def _on_carry_forward(self, session: api_client.SessionRow) -> None:
        """Plan a fresh item for today, then cancel yesterday's leftover —
        it's superseded, not skipped (the work is still intended, just on a
        new day), and cancel is what keeps it from sitting as "planned"
        forever once it ages out of the missedYesterday window."""

        def work() -> api_client.SessionRow:
            new_session = api_client.plan_session(
                session["taskId"], session["plannedMinutes"], focus_text=session.get("focusText")
            )
            api_client.cancel_session(session["id"])
            return new_session

        self._write(work, "Add")


def load_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_path(str(Path(__file__).parent / "style.css"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def main() -> int:
    # gtk-layer-shell does not fail loudly when the compositor has not
    # advertised zwlr_layer_shell_v1 yet: it falls back to a plain XDG
    # toplevel. That fallback is worse than not starting at all — the panel
    # comes up as a 0-height toplevel that draws nothing, takes focus (its
    # title shows up in Waybar's window module), and can never be revealed,
    # so every SIGUSR1 toggles an invisible window forever. Niri reaches
    # graphical-session.target before that global exists, which is why every
    # niri-*.service here — Waybar included — crash-loops for the first
    # half-minute of a session. Exit non-zero and let Restart=on-failure
    # retry in 2s instead of running a permanently broken window.
    if not GtkLayerShell.is_supported():
        print(
            "layer shell unavailable — compositor not ready yet, retrying",
            file=sys.stderr,
        )
        return 1

    load_css()
    widget = TaskWidget()
    app = Application("persona-tasks-widget", widget.window)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
