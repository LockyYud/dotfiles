"""
Always-visible Persona Assistant task panel — a small GTK3 layer-shell
overlay (top-right, non-exclusive so it never steals screen space like
Waybar does) built with Fabric. Reads/writes tasks through api_client.py,
which talks to the same worker /desktop/* routes as the Vicinae "My Tasks"
extension, using the same desktop token and config.env.

Toggled on/off by sending SIGUSR1 to this process — see
persona-tasks-widget-toggle, wired to Waybar's left-click and Mod+Shift+T.

Grouping model — sections are the task's STATUS, not its schedule. The
worker still returns schedule buckets (overdue/today/future/unscheduled),
because the daily briefing and the web list group that way; here they are
flattened back into one list and re-grouped as "In progress" then "Open",
so the panel answers "what am I working on" before "what is late". The
schedule has not been thrown away: it orders the tasks inside each section
(overdue first, then today, then future, then undated) and every block still
carries its own red "overdue by 2h" line.

An earlier version had no status axis at all — a scheduled task was treated
as in-progress and an unscheduled one as open. That inferred a status these
tasks already carry, and got it wrong in both directions.

Layout model — a task with subtasks renders as ONE block, not as sibling
rows: the parent is context (ticket badge, title, subtask meter) and its
nextStep is the actionable line beneath it, tied together by a shared
priority-colored left border. Subtasks therefore never appear as top-level
rows; anything carrying parentTaskId is filtered out of the sections.
"""

import re
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

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
# Slightly longer than the revealer's own 140ms slide, so the height is read
# after the transition has settled.

# Durations offered for a routine's day. The pace's own suggestion is added in
# front of these at render time, so the common case is one click on a number
# that already accounts for what is left and how much of the month remains.
PLAN_OPTIONS = [("30m", 30), ("1h", 60), ("2h", 120)]
# Monthly targets offered when designating a routine. Hours, because that is
# the unit the target is thought in ("20 hours of English a month") even though
# everything downstream stores minutes.
MONTHLY_TARGET_OPTIONS = [("5h", 5 * 60), ("10h", 10 * 60), ("20h", 20 * 60), ("40h", 40 * 60)]
PRIORITY_CLASSES = {"urgent", "high", "medium", "low"}

STATUS_IN_PROGRESS = "in_progress"
STATUS_OPEN = "open"
# Section order is the whole point of the status grouping: what you are
# already working on comes before what you could pick up. The worker never
# returns done/cancelled tasks, so these two cover everything; anything with
# an unrecognised status is treated as open rather than dropped, so a new
# status added upstream degrades to "visible" instead of "invisible".
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

        # The Open section is the backlog you pick from, so it starts expanded
        # — collapsing it by default would leave the panel empty until
        # something is in progress. Collapsed state persists for the session.
        self.open_expanded = True
        self._last_now: api_client.NowTasks | None = None
        # Which task's monthly-target picker is open, if any. One at a time:
        # designating a routine is a deliberate, occasional act, and letting
        # several pickers stand open would just make the panel taller.
        self._target_picker_for: str | None = None
        # (task_id, "plan" | "target") while a free-text field is open. Chips
        # cover the common amounts; this is for any other number.
        self._entry_for: tuple[str, str] | None = None
        # Today's sessions, or None while the sessions half has never been
        # fetched successfully — the panel still draws without it.
        self._last_today: api_client.Today | None = None
        self._last_sync: datetime | None = None
        self._failure_streak = 0
        # Set when a write (start/stop, complete, plan) is rejected. Those
        # all render optimistically, so without this the task simply snaps
        # back at the next poll and the widget looks broken rather than
        # honest about having failed.
        self._write_error: str | None = None
        self.footer_label: Label | None = None

        self.header_title = Label(label="NOW", style_classes="header", h_align="start", h_expand=True)
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
        self.header_title.set_label("NOW")
        self.body.add(Label(label="Loading…", style_classes="state-message", h_align="start"))
        self.body.show_all()
        GLib.idle_add(self._sync_body_height)

    def render_message(self, message: str, error: bool = False) -> None:
        self._clear()
        self.header_title.set_label("NOW")
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
            # Held apart from _last_now so every existing optimistic update
            # (which rewrites NowTasks in place) keeps working untouched.
            self._last_today = panel["today"]
            # Re-rendering rebuilds every widget, which would destroy an open
            # entry mid-keystroke. The poll's data is kept; the view catches up
            # as soon as the field closes.
            if self._entry_for is None:
                self.render(panel["now"])
            else:
                self._last_now = panel["now"]

    def render(self, now: api_client.NowTasks) -> None:
        self._last_now = now
        self._clear()

        grouped = group_by_status(all_tasks(now))
        in_progress = grouped[STATUS_IN_PROGRESS]
        open_tasks = grouped[STATUS_OPEN]
        routine_tasks = routines(now)

        # The header counts what you are working on, not the whole backlog:
        # "NOW · 2" next to an Open section of 30 reads as the useful number.
        # Routines count too — a routine is in progress by definition, and
        # leaving them out would make this disagree with the Waybar module.
        self.header_title.set_label(f"NOW · {len(in_progress) + len(routine_tasks)}")

        # Today's plan goes first: it is the one section you act on every day,
        # and it is short by nature. Routines are also exempt from
        # MAX_TASKS_SHOWN — there are only ever a handful, and dropping one
        # would hide the only signal a routine can produce.
        if routine_tasks:
            behind = sum(1 for task in routine_tasks if task["pace"]["status"] == "behind")
            self.body.add(
                self._build_section_title("Routines", "routines", len(routine_tasks), 0)
            )
            if behind:
                self.body.add(
                    Label(
                        label=f"{behind} behind this month",
                        style_classes="section-subnote",
                        h_align="start",
                    )
                )
            for task in routine_tasks:
                self.body.add(self._build_routine_block(task))

        shown = 0
        for status, title, tone in STATUS_SECTIONS:
            tasks = in_progress if status == STATUS_IN_PROGRESS else open_tasks
            if not tasks:
                continue

            overdue_count = sum(1 for t in tasks if t.get("dueAt") and is_overdue(t["dueAt"]))
            # Open is the backlog and can be long, so it collapses; In progress
            # is the reason the panel exists and always stays open.
            if status == STATUS_OPEN:
                self.body.add(self._build_section_toggle(title, tone, len(tasks), overdue_count))
                if not self.open_expanded:
                    continue
            else:
                self.body.add(self._build_section_title(title, tone, len(tasks), overdue_count))

            for task in tasks:
                if shown >= MAX_TASKS_SHOWN:
                    break
                self.body.add(self._build_task_block(task))
                shown += 1

        if not in_progress and not open_tasks and not routine_tasks:
            self.body.add(self._build_empty_state())

        # Two ways a task can be missing from the list above: the local
        # MAX_TASKS_SHOWN cap, and the worker's own per-bucket caps (which
        # only unscheduledCount reports on). Both are surfaced rather than
        # letting the panel imply the list is complete.
        received = len(in_progress) + len(open_tasks)
        hidden_by_worker = max(0, now["unscheduledCount"] - len(top_level(now["unscheduled"])))
        hidden = max(0, received - shown) + hidden_by_worker
        if hidden > 0 and (self.open_expanded or in_progress):
            self.body.add(
                Label(
                    label=f"+{hidden} more not shown",
                    style_classes="footer-note",
                    h_align="start",
                )
            )

        self.footer_label = Label(label="", style_classes="sync-note", h_align="start")
        self.body.add(self.footer_label)
        self._refresh_footer()

        self.body.show_all()
        # Deferred: preferred-height only reflects the new children after the
        # pending resize has been processed.
        GLib.idle_add(self._sync_body_height)

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

    def _build_empty_state(self) -> Box:
        row = Box(orientation="h", spacing=10, style_classes="empty-state")
        row.add(Label(label="✓", style_classes="empty-check", v_align="start"))
        text = Box(orientation="v", spacing=1)
        text.add(Label(label="All clear", style_classes="empty-title", h_align="start"))
        text.add(Label(label="Nothing due right now", style_classes="empty-subtitle", h_align="start"))
        row.add(text)
        return row

    def _section_header_content(self, title: str, count: int, overdue_count: int) -> Box:
        """Section label plus its counts.

        The overdue tally lives here because status is now the grouping axis:
        without it you would have to scroll a long Open section to find out
        anything in it is late.
        """
        content = Box(orientation="h", spacing=6)
        content.add(
            Label(
                label=f"{title} · {count}",
                style_classes="section-label",
                h_align="start",
                h_expand=True,
            )
        )
        if overdue_count:
            content.add(
                Label(label=f"{overdue_count} overdue", style_classes="section-overdue-badge")
            )
        return content

    def _build_section_title(self, title: str, tone: str, count: int, overdue_count: int) -> Box:
        row = Box(orientation="h", style_classes=f"section-title {tone}")
        row.add(self._section_header_content(title, count, overdue_count))
        return row

    def _build_section_toggle(self, title: str, tone: str, count: int, overdue_count: int) -> Button:
        content = self._section_header_content(title, count, overdue_count)
        content.add(
            Label(label="▴" if self.open_expanded else "▾", style_classes="section-arrow")
        )
        toggle = Button(child=content, style_classes=f"section-title section-toggle {tone}")
        toggle.connect("clicked", self._toggle_open_expanded)
        return toggle

    def _toggle_open_expanded(self, *_args) -> None:
        self.open_expanded = not self.open_expanded
        if self._last_now is not None:
            self.render(self._last_now)

    def _build_meter(self, progress: api_client.Progress) -> Box:
        """Subtask roll-up. Pips stay countable up to PIP_MAX_TOTAL; past that
        a proportional bar carries the ratio better than a row of dots. The
        done/total fraction is always spelled out, so the exact number never
        depends on reading the graphic."""
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
            # A parent is a container: closing it while subtasks are open would
            # silently orphan them. Tick the subtask instead and the meter here
            # advances on its own.
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
        """The open <-> in_progress toggle.

        Both directions are one click, because a mis-click here writes through
        to Notion — an unreversible "start" would be a trap. The two share a
        slot so a block's width never depends on its status.
        """
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

    def _build_task_block(self, task: api_client.TaskRow) -> EventBox:
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

        # Line 1 — identity and progress, both scannable without reading the
        # title: ticket key, work/personal, subtask meter.
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
        # The deadline lives here now that the schedule row is gone. It is
        # information, not a control, and the head is where the block's other
        # scannable facts already sit — so nothing about "what is late" was
        # lost when the row it used to occupy became today's minutes.
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

        # Line 2 — the title itself, wrapped rather than ellipsized so a long
        # Notion title stays readable, and the complete action beside it.
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
        title_row.add(self._build_routine_toggle(task))
        title_row.add(self._build_status_button(task))
        title_row.add(self._build_complete_button(task, progress))
        block.add(title_row)

        # Line 3 — today. Always open: it is the control the panel exists for,
        # and hiding it behind a hover is what made it undiscoverable when the
        # chips were first added.
        block.add(self._build_today_row(task))

        session = self._session_for(task["id"])
        if session is not None:
            block.add(self._build_session_row(session))

        if self._target_picker_for == task["id"]:
            block.add(
                self._build_inline_entry(task, "target")
                if self._entry_for == (task["id"], "target")
                else self._build_target_picker(task)
            )

        # Line 4 — the subtask you'd actually act on next. Indented under the
        # parent and sharing its accent border, so the two read as one unit.
        next_step = task.get("nextStep")
        if next_step:
            step_row = Box(orientation="h", spacing=6, style_classes="next-step-row")
            step_row.add(Label(label="└", style_classes="step-connector", v_align="start"))
            step_row.add(
                Label(
                    label=next_step["title"],
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

    def _open_entry(self, task_id: str, mode: str) -> None:
        self._entry_for = (task_id, mode)
        if self._last_now is not None:
            self.render(self._last_now)

    def _close_entry(self) -> None:
        self._entry_for = None
        if self._last_now is not None:
            self.render(self._last_now)

    def _build_more_chip(self, task_id: str, mode: str) -> Button:
        """Opens the free-text field. Chips are the fast path for the amounts
        you reach for most; this is the way to say any other number."""
        chip = Button(
            label="⋯",
            style_classes="plan-btn",
            tooltip_text=(
                "Type any number of hours per month"
                if mode == "target"
                else "Type any length — 90, 1h30, 45m"
            ),
        )
        chip.connect("clicked", lambda _btn, t=task_id, m=mode: self._open_entry(t, m))
        return chip

    def _build_inline_entry(self, task: api_client.TaskRow, mode: str) -> Box:
        """A one-line field replacing the chips, committed with Enter.

        Escape closes it. So does a value that will not parse, except that the
        field stays open and marked instead — losing what was typed because a
        stray character slipped in would be worse than the typo.
        """
        is_target = mode == "target"
        pace = task.get("pace")
        if is_target:
            initial = f"{pace['targetMinutes'] / 60:g}" if pace else ""
        else:
            session = self._session_for(task["id"])
            suggested = pace["suggestedTodayMinutes"] if pace else 0
            initial = str(session["plannedMinutes"] if session else suggested or "")

        row = Box(orientation="h", spacing=6, style_classes="inline-entry-row")
        row.add(
            Label(
                label="hours/month" if is_target else "minutes today",
                style_classes="target-picker-label",
                h_align="start",
            )
        )
        entry = Entry(
            text=initial,
            placeholder="20" if is_target else "90 or 1h30",
            style_classes="inline-entry",
            h_expand=True,
        )

        def commit(_widget=None) -> None:
            raw = entry.get_text()
            minutes = parse_target_minutes(raw) if is_target else parse_duration_minutes(raw)
            if minutes is None:
                entry.add_style_class("invalid")
                return
            self._entry_for = None
            if is_target:
                self._target_picker_for = None
                self._on_set_routine(task["id"], minutes)
            else:
                self._on_plan(task["id"], minutes)

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

        # The field is opened by a click, so it has to take focus itself —
        # idled because it does not exist on screen until this render lands.
        GLib.idle_add(entry.grab_focus)
        return row

    def _build_today_row(self, task: api_client.TaskRow):
        """How much of today goes to this task — on every block, not just
        routines.

        This replaced the schedule row (due time plus snooze chips). Deciding
        what today actually holds is the thing done every morning; nudging a
        deadline by an hour is not, and a deadline that still matters is now
        carried in the block's head where it costs no room. The same control
        for a routine and for an ordinary task keeps one habit rather than two.
        """
        session = self._session_for(task["id"])
        typing = self._entry_for == (task["id"], "plan")
        row = Box(orientation="h", spacing=6, style_classes="task-meta-row")
        if typing:
            return self._build_inline_entry(task, "plan")
        row.add(self._plan_chips(task, session))
        return row

    def _build_routine_toggle(self, task: api_client.TaskRow) -> Button:
        """Opens the monthly-target picker for one task.

        Present on every block, routine or not, because this is the only place
        outside chat where a task can be made into a routine — and having to
        open a chat window to say "measure this monthly" was the whole reason
        it moved here.
        """
        is_routine = task.get("pace") is not None
        btn = Button(
            label="⟳",
            style_classes="status-btn" + (" started" if is_routine else ""),
            tooltip_text=(
                "Change the monthly target, or stop measuring it"
                if is_routine
                else "Make this a routine — measured in hours per month"
            ),
        )
        btn.connect("clicked", lambda _btn, task_id=task["id"]: self._toggle_target_picker(task_id))
        return btn

    def _toggle_target_picker(self, task_id: str) -> None:
        self._target_picker_for = None if self._target_picker_for == task_id else task_id
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
        row.add(self._build_more_chip(task["id"], "target"))
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

    def _session_for(self, task_id: str) -> api_client.SessionRow | None:
        if self._last_today is None:
            return None
        for session in self._last_today["sessions"]:
            if session["taskId"] == task_id:
                return session
        return None

    def _plan_chips(self, task: api_client.TaskRow, session) -> Box:
        """Duration chips for a routine's day.

        Exactly one chip is ever highlighted, and it always means the same
        thing: *this is what today is set to* — including the ⊘ when today is
        set to "not doing it". Only when nothing at all is set does the
        highlight fall on the pace's suggestion, which is then the obvious
        click. Highlighting the suggestion while a different amount was already
        planned made the row contradict the line right below it — "30m planned
        today" under a lit-up 53m reads as though the 53m had been chosen.

        The suggestion still leads the row when it is not one of the fixed
        options, so it stays one click away either way. Re-planning replaces
        the commitment rather than adding to it, so these are totals, not
        top-ups.
        """
        row = Box(orientation="h", spacing=6)
        # Only a routine has a suggestion; every other task just gets the fixed
        # options, with whatever is already set for today lit.
        pace = task.get("pace")
        suggested = pace["suggestedTodayMinutes"] if pace else 0
        # A skipped day is not a commitment, so no duration is "what today is
        # set to" — the lit chip is the ⊘ instead, and lighting the suggestion
        # alongside it would put two answers to the same question in one row.
        missed = session is not None and session["status"] == "skipped"
        planned = session["plannedMinutes"] if session is not None and not missed else None

        options = list(PLAN_OPTIONS)
        for extra in (suggested, planned):
            if extra and extra not in [minutes for _, minutes in options]:
                options.insert(0, (fmt_minutes(extra), extra))

        for label_text, minutes in options:
            if missed:
                current = False
            elif planned is not None:
                current = minutes == planned
            else:
                current = minutes == suggested and suggested > 0

            if planned is not None:
                verb = "Already set to" if current else "Change to"
            else:
                verb = "Plan"
            hint = "" if planned is not None or not current else " (suggested)"

            chip = Button(
                label=label_text,
                style_classes="plan-btn current" if current else "plan-btn",
                tooltip_text=f"{verb} {fmt_minutes(minutes)} today{hint}",
            )
            chip.connect(
                "clicked",
                lambda _btn, task_id=task["id"], m=minutes: self._on_plan(task_id, m),
            )
            row.add(chip)
        row.add(self._build_more_chip(task["id"], "plan"))
        miss = self._build_miss_chip(task, session, suggested)
        if miss is not None:
            row.add(miss)
        return row

    def _build_miss_chip(self, task: api_client.TaskRow, session, suggested: int):
        """"Not today" for a routine, in one click — or None when there is
        nothing to pass on.

        A routine's day is did-it-or-not, so declining one has to cost the same
        as committing to it. Before this the only way to record a miss was to
        plan an amount and then skip that amount: two clicks to say you did
        nothing.

        Deliberately absent in three cases. An ordinary task never had a
        commitment to decline. A day already planned is handled by the session
        row's own skip button, and a second identical control beside it would
        only raise the question of whether they differ. And a month already at
        target has nothing left to miss.
        """
        if task.get("pace") is None:
            return None

        missed = session is not None and session["status"] == "skipped"
        if missed:
            chip = Button(
                label="⊘",
                style_classes="plan-btn current",
                tooltip_text="Marked as not done today",
            )
            chip.set_sensitive(False)
            return chip

        if session is not None or suggested <= 0:
            return None

        chip = Button(
            label="⊘",
            style_classes="plan-btn",
            tooltip_text="Not today — recorded as a miss, not held against the month",
        )
        chip.connect(
            "clicked",
            lambda _btn, task_id=task["id"], m=suggested: self._on_miss(task_id, m),
        )
        return chip

    def _build_routine_block(self, task: api_client.TaskRow):
        """A routine, with today's session as the actionable line beneath it.

        Deliberately not rendered as a normal task block. The ✓ there calls
        complete_task, which would mark a routine finished forever, and a
        routine's month takes the line where a dated task carries nothing —
        so the affordances differ even though the shape should not.
        """
        pace = task["pace"]
        session = self._session_for(task["id"])
        tone = "behind" if pace["status"] == "behind" else pace["status"].replace("_", "-")

        block = Box(
            orientation="v",
            spacing=2,
            style_classes=f"task-block {priority_class(task['priority'], 'accent')} routine {tone}",
        )

        head = Box(orientation="h", spacing=6, style_classes="task-head")
        head.add(
            Label(
                label=task.get("type") or "",
                style_classes="type-label",
                h_align="start",
                h_expand=True,
                ellipsization="end",
            )
        )
        head.add(
            Label(
                label=f"day {pace['dayOfMonth']}/{pace['daysInMonth']}",
                style_classes="routine-day",
            )
        )
        block.add(head)

        title_row = Box(orientation="h", spacing=6)
        title_row.add(
            Label(
                label=clamp_title(task["title"]),
                style_classes="task-title",
                h_align="start",
                h_expand=True,
                justification="left",
                line_wrap="word-char",
                size=(TITLE_WIDTH, -1),
            )
        )
        title_row.add(self._build_routine_toggle(task))
        block.add(title_row)

        # A routine's month is its equivalent of a deadline, so it sits on its
        # own line above today's control — the same place a dated task now
        # carries nothing, its deadline having moved up into the head.
        pace_row = Box(orientation="h", spacing=6, style_classes="task-meta-row")
        pace_row.add(
            Label(
                label=describe_pace(pace),
                style_classes="task-meta" + (" overdue" if pace["status"] == "behind" else ""),
                h_align="start",
                h_expand=True,
            )
        )
        block.add(pace_row)

        block.add(self._build_today_row(task))

        if self._target_picker_for == task["id"]:
            block.add(
                self._build_inline_entry(task, "target")
                if self._entry_for == (task["id"], "target")
                else self._build_target_picker(task)
            )

        if session is not None:
            block.add(self._build_session_row(session))
        elif pace["suggestedTodayMinutes"] > 0:
            # Nothing promised yet: say what today wants rather than leaving
            # the block silent about it.
            block.add(
                Label(
                    label=f"nothing planned today · suggest {fmt_minutes(pace['suggestedTodayMinutes'])}",
                    style_classes="routine-unplanned",
                    h_align="start",
                )
            )

        hover_box = EventBox(events=["enter-notify", "leave-notify"], child=block)
        hover_box.connect(
            "enter-notify-event", lambda _w, event: self._set_block_hovered(event, block, True)
        )
        hover_box.connect(
            "leave-notify-event", lambda _w, event: self._set_block_hovered(event, block, False)
        )
        return hover_box

    def _build_session_row(self, session: api_client.SessionRow) -> Box:
        """Today's session, indented under its routine like a next step."""
        planned = fmt_minutes(session["plannedMinutes"])
        if session["status"] == "done":
            actual = session["actualMinutes"] or session["plannedMinutes"]
            text = (
                f"{fmt_minutes(actual)} done"
                if actual == session["plannedMinutes"]
                else f"{fmt_minutes(actual)} done of {planned}"
            )
        elif session["status"] == "skipped":
            text = "skipped today"
        else:
            text = f"{planned} planned today"

        row = Box(orientation="h", spacing=6, style_classes="next-step-row")
        row.add(Label(label="└", style_classes="step-connector", v_align="start"))
        row.add(
            Label(
                label=text,
                style_classes="step-title" + (" done" if session["status"] == "done" else ""),
                h_align="start",
                h_expand=True,
                ellipsization="end",
            )
        )

        if session["status"] == "planned":
            done_btn = Button(
                label="✓",
                style_classes="complete-btn",
                tooltip_text=f"Done — credit {planned}",
            )
            done_btn.connect(
                "clicked",
                lambda _btn, sid=session["id"]: self._on_complete_session(sid),
            )
            row.add(done_btn)
            skip_btn = Button(
                label="⊘",
                style_classes="plan-btn",
                tooltip_text="Skip today (does not count against the month)",
            )
            skip_btn.connect(
                "clicked",
                lambda _btn, sid=session["id"]: self._on_skip_session(sid),
            )
            row.add(skip_btn)
        return row

    def _set_block_hovered(self, event, block: Box, hovered: bool) -> bool:
        """Only a style class now.

        Every row a block owns is permanently visible since today's minutes
        replaced the schedule row, so nothing reveals or collapses and the
        block's height never moves — which also means the body height no
        longer has to be re-synced on hover.
        """
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

    # No optimistic render for the three session writes below. Every one of
    # them changes the routine's pace, and suggestedTodayMinutes depends on
    # arithmetic over the whole month that this panel deliberately does not
    # reimplement — a guessed number would be shown and then visibly jump when
    # the poll landed. _write re-polls on success, so the correct numbers
    # arrive a moment later instead.
    def _on_set_routine(self, task_id: str, minutes: int | None) -> None:
        # Closed eagerly: the picker's job is done, and leaving it open over a
        # block that is about to move into another section reads as a glitch.
        self._target_picker_for = None
        label = "Make routine" if minutes is not None else "Stop measuring"
        self._write(lambda: api_client.set_routine_target(task_id, minutes), label)

    def _on_plan(self, task_id: str, minutes: int) -> None:
        self._write(lambda: api_client.plan_session(task_id, minutes), "Plan")

    def _on_complete_session(self, session_id: str) -> None:
        self._write(lambda: api_client.complete_session(session_id), "Done")

    def _on_skip_session(self, session_id: str) -> None:
        self._write(lambda: api_client.skip_session(session_id), "Skip")

    def _on_miss(self, task_id: str, minutes: int) -> None:
        self._write(lambda: api_client.mark_missed(task_id, minutes), "Miss")



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
