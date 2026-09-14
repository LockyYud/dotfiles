"""
Reads the same ~/.config/persona-assistant/config.env and desktop-token
files as persona-config / persona-tasks-status / the Vicinae "My Tasks"
extension — one source of truth for where Persona Assistant lives and how
this machine authenticates to it. Ported line-for-line from
dotfiles/vicinae-extensions/my-tasks/src/lib/api-client.ts; keep the two in
sync if the worker's /desktop/* contract changes.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, TypedDict

CONFIG_DIR = Path.home() / ".config" / "persona-assistant"
CONFIG_PATH = CONFIG_DIR / "config.env"
TOKEN_PATH = CONFIG_DIR / "desktop-token"


def _read_config() -> dict[str, str]:
    try:
        raw = CONFIG_PATH.read_text()
    except OSError:
        return {}

    config: dict[str, str] = {}
    for line in raw.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        key, sep, value = trimmed.partition("=")
        if not sep:
            continue
        config[key.strip()] = value.strip()
    return config


_config = _read_config()
WORKER_URL = _config.get("PERSONA_WORKER_URL", "http://localhost:8787")
APP_URL = _config.get("PERSONA_APP_URL", "http://localhost:3000")


class Progress(TypedDict):
    """Subtask roll-up mirroring Notion's Progress column."""

    done: int
    total: int


class Pace(TypedDict):
    """How a routine's month is going, worked out by the worker.

    Nothing here is recomputed locally — suggestedTodayMinutes in particular
    depends on the whole month's arithmetic, and a second implementation would
    eventually disagree with the briefing and the chat agent about the same
    day.
    """

    targetMinutes: int
    spentMinutes: int
    expectedMinutes: int
    # Negative means behind.
    deltaMinutes: int
    status: str  # "ahead" | "on_track" | "behind"
    dayOfMonth: int
    daysInMonth: int
    suggestedTodayMinutes: int


class TaskRow(TypedDict):
    id: str
    title: str
    description: Optional[str]
    status: str
    priority: str
    type: str  # "work" | "personal"
    dueAt: Optional[str]
    # Set on subtasks only; the widget filters these out of the top-level
    # sections so a subtask never renders as a sibling of its own parent.
    parentTaskId: Optional[str]
    notionPageId: Optional[str]
    # Both are None on tasks that have no subtasks at all.
    progress: Optional[Progress]
    nextStep: Optional["TaskRow"]
    # Set only on a routine — a task pursued at a rate per month rather than
    # finished once. None on every ordinary task.
    monthlyTargetMinutes: Optional[int]
    pace: Optional[Pace]


class NowTasks(TypedDict):
    """The worker's schedule buckets.

    The widget groups by *status*, so it reads these only to reassemble one
    flat list — see main.py's all_tasks(). `nextUp` is deliberately ignored
    there: it duplicates future[0], and counting it separately would render
    the same task twice.
    """

    overdue: list[TaskRow]
    today: list[TaskRow]
    nextUp: Optional[TaskRow]
    future: list[TaskRow]
    # Routines being actively pursued. They carry no dueAt, so they appear in
    # none of the buckets above and all_tasks() deliberately does not walk
    # them: the panel gives them their own section, because the actions that
    # make sense on a routine are not the ones that make sense on a task with
    # a deadline.
    ongoing: list[TaskRow]
    unscheduledCount: int
    unscheduled: list[TaskRow]


class SessionRow(TypedDict):
    """One Today item — a planned/done/skipped/cancelled slice of a day,
    with the task it belongs to.

    Today Plan v1: a task can now have several of these on the same day
    (`position` orders them within the day), and `sessionId` — not
    (taskId, date) — is what makes a plan_session() call a revise instead of
    a new item. `focusText` is what the item is actually about today, which
    may be narrower than the task's own title (e.g. "Run baseline" on a task
    titled "RAG Pipeline Lab").
    """

    id: str
    taskId: str
    date: str
    startAt: Optional[str]
    position: int
    focusText: Optional[str]
    plannedMinutes: int
    actualMinutes: Optional[int]
    status: str  # "planned" | "done" | "skipped" | "cancelled"
    task: TaskRow


class Today(TypedDict):
    date: str
    timezone: str
    sessions: list[SessionRow]
    # Yesterday's items still "planned" when their day ended — neither done,
    # skipped, nor cancelled. Surfaced so the panel can offer to carry them
    # forward instead of quietly losing them.
    missedYesterday: list[SessionRow]
    ongoing: list[TaskRow]


class Panel(TypedDict):
    """Everything the panel draws, in one shape.

    `today` is Optional because the sessions call is additive: the task list is
    the reason this panel exists, so a failure fetching sessions must degrade
    to "no session info" rather than blanking the whole window.
    """

    now: NowTasks
    today: Optional[Today]


class NoDesktopTokenError(Exception):
    """Raised when ~/.config/persona-assistant/desktop-token is missing."""


class DesktopTokenRevokedError(Exception):
    """Raised on a 401 from a /desktop/* route."""


class RequestFailedError(Exception):
    """Raised on any other non-2xx response."""


def _read_token() -> str:
    try:
        return TOKEN_PATH.read_text().strip()
    except OSError as error:
        raise NoDesktopTokenError("No desktop token found — run persona-connect first.") from error


def _request(path: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    token = _read_token()
    data = json.dumps(body).encode() if body is not None else None

    request = urllib.request.Request(
        f"{WORKER_URL}{path}",
        data=data,
        method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise DesktopTokenRevokedError(
                "Desktop token invalid or revoked — run persona-connect again."
            ) from error
        raise RequestFailedError(f"Request failed ({error.code})") from error


def fetch_now_tasks() -> NowTasks:
    data = _request("/desktop/tasks/now")
    return data["now"]


def fetch_today() -> Today:
    return _request("/desktop/today")


def fetch_panel() -> Panel:
    """Both halves of the panel in one background call.

    Token errors from the first request propagate, since they are fatal for
    everything; a later failure on the sessions half is swallowed so the task
    list still renders.
    """
    # Debug seam, mirroring the Waybar module's PERSONA_TASKS_FIXTURE: point
    # this at a saved {"now": ..., "today": ...} document to render it without
    # a worker or a token. The only way to exercise the routine layout before
    # any routine exists.
    fixture = os.environ.get("PERSONA_PANEL_FIXTURE")
    if fixture:
        with open(fixture, encoding="utf-8") as handle:
            return json.load(handle)

    now = fetch_now_tasks()
    try:
        today: Optional[Today] = fetch_today()
    except (RequestFailedError, urllib.error.URLError, TimeoutError, OSError):
        today = None
    return {"now": now, "today": today}


def plan_session(
    task_id: str,
    planned_minutes: int,
    *,
    session_id: Optional[str] = None,
    date: Optional[str] = None,
    focus_text: Optional[str] = None,
    start_at: Optional[str] = None,
) -> SessionRow:
    """Add or revise a Today item.

    Not idempotent per (task, day) any more — a task can carry several items
    on the same day. Omitting `session_id` always appends a new item; passing
    one revises that specific item instead (and only while it is still
    "planned" — the worker 400s on trying to revise a done/skipped/cancelled
    one). `date` defaults to the caller's local today on the worker side.
    """
    body: dict = {"taskId": task_id, "plannedMinutes": planned_minutes}
    if session_id is not None:
        body["sessionId"] = session_id
    if date is not None:
        body["date"] = date
    if focus_text is not None:
        body["focusText"] = focus_text
    if start_at is not None:
        body["startAt"] = start_at
    return _request("/desktop/sessions", method="POST", body=body)["session"]


def mark_missed(task_id: str, planned_minutes: int) -> None:
    """Record today as deliberately not done, with nothing planned yet.

    Two calls: plan_session now always appends, so this creates a fresh item
    and immediately skips it rather than revising anything. `plannedMinutes`
    is what the day was asking for, so the record says "wanted 53m, did none"
    rather than inventing a commitment.
    """
    skip_session(plan_session(task_id, planned_minutes)["id"])


def complete_session(session_id: str, actual_minutes: Optional[int] = None) -> SessionRow:
    """Close a session out; omitting the minutes credits what was committed to."""
    body = {} if actual_minutes is None else {"actualMinutes": actual_minutes}
    return _request(f"/desktop/sessions/{session_id}/complete", method="POST", body=body)["session"]


def skip_session(session_id: str) -> SessionRow:
    """A deliberate pass — the work was consciously not done. Excluded from
    the month's pace numerator, but distinct from cancel: the plan itself
    was still the right one."""
    return _request(f"/desktop/sessions/{session_id}/skip", method="POST")["session"]


def cancel_session(session_id: str) -> SessionRow:
    """Remove a still-planned item because the plan changed — wrong pick,
    task de-prioritized — not because the work was skipped. Only valid while
    the item is still "planned"."""
    return _request(f"/desktop/sessions/{session_id}/cancel", method="POST")["session"]


def set_routine_target(task_id: str, monthly_target_minutes: Optional[int]) -> None:
    """Designate a task as a routine, or stop measuring it as one.

    Its own narrow route rather than a general task update: the desktop token
    lives in a file on a laptop, so it may read, complete, restatus, plan a
    day — and now designate — and nothing else.

    Setting a target also starts the task if it was merely open, because a
    routine only reaches the panel while it is in_progress. Clearing one does
    not stop the task: no longer measuring something monthly is not the same
    as no longer doing it.
    """
    _request(
        f"/desktop/tasks/{task_id}/routine",
        method="POST",
        body={"monthlyTargetMinutes": monthly_target_minutes},
    )


def complete_task(task_id: str) -> None:
    _request(f"/desktop/tasks/{task_id}/complete", method="POST")


def set_task_status(task_id: str, status: str) -> None:
    """Move a task between "open" and "in_progress".

    The route rejects "done"/"cancelled" on purpose — completing goes through
    complete_task, which also settles reminders and rolls the parent's
    progress up. The worker pushes the new status to the task's Notion page,
    so starting a task here is visible in Notion too.
    """
    _request(f"/desktop/tasks/{task_id}/status", method="POST", body={"status": status})


def create_task(title: str, due_at_iso: Optional[str] = None, priority: str = "medium") -> None:
    body = {"title": title, "priority": priority}
    if due_at_iso:
        body["dueAt"] = due_at_iso
    _request("/desktop/tasks", method="POST", body=body)
