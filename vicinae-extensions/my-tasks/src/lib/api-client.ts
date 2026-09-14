import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const CONFIG_DIR = join(homedir(), ".config", "persona-assistant");
const CONFIG_PATH = join(CONFIG_DIR, "config.env");
const TOKEN_PATH = join(CONFIG_DIR, "desktop-token");

// Same config.env format read by persona-config/persona-tasks-open/
// persona-tasks-status (dotfiles/.local/bin) — one source of truth for
// where Persona Assistant actually lives, never a hardcoded localhost URL.
function readConfig(): Record<string, string> {
  let raw: string;
  try {
    raw = readFileSync(CONFIG_PATH, "utf8");
  } catch {
    return {};
  }

  const config: Record<string, string> = {};
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    config[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return config;
}

const config = readConfig();
const WORKER_URL = config.PERSONA_WORKER_URL ?? "http://localhost:8787";
export const APP_URL = config.PERSONA_APP_URL ?? "http://localhost:3000";

/**
 * How a routine's month is going. Sent by the worker already worked out —
 * including how many minutes to suggest for today — so nothing here recomputes
 * it and the widget can never disagree with the briefing or the chat agent.
 */
export interface Pace {
  targetMinutes: number;
  spentMinutes: number;
  expectedMinutes: number;
  /** Negative means behind. */
  deltaMinutes: number;
  status: "ahead" | "on_track" | "behind";
  dayOfMonth: number;
  daysInMonth: number;
  suggestedTodayMinutes: number;
}

export interface TaskRow {
  id: string;
  title: string;
  status: string;
  priority: string;
  dueAt: string | null;
  /** Set only on a routine — a task pursued at a rate per month. */
  monthlyTargetMinutes?: number | null;
  /** Present iff the task is a routine; null on ordinary tasks. */
  pace?: Pace | null;
}

export interface NowTasks {
  overdue: TaskRow[];
  today: TaskRow[];
  nextUp: TaskRow | null;
  /**
   * Routines being actively pursued. They carry no dueAt, so they are in none
   * of the buckets above — a view that reads only those would tell the user
   * "all clear" while a routine sat weeks behind.
   */
  ongoing: TaskRow[];
  unscheduledCount: number;
}

export type SessionStatus = "planned" | "done" | "skipped";

/** One day's committed work on a task, with the task it belongs to. */
export interface SessionRow {
  id: string;
  taskId: string;
  date: string;
  startAt: string | null;
  plannedMinutes: number;
  actualMinutes: number | null;
  status: SessionStatus;
  task: TaskRow;
}

/**
 * The widget's home screen. Both halves are returned because they answer
 * different questions — what has been committed to today, and what the month
 * still wants — and comparing them is how "1h planned of the 1h45m today
 * wants" gets said.
 */
export interface Today {
  date: string;
  timezone: string;
  sessions: SessionRow[];
  ongoing: TaskRow[];
}

function readToken(): string {
  try {
    return readFileSync(TOKEN_PATH, "utf8").trim();
  } catch {
    throw new Error("No desktop token found — run persona-connect first.");
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = readToken();
  const response = await fetch(`${WORKER_URL}${path}`, {
    ...init,
    headers: { ...init.headers, authorization: `Bearer ${token}`, "content-type": "application/json" },
  });

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error("Desktop token invalid or revoked — run persona-connect again.");
    }
    throw new Error(`Request failed (${response.status})`);
  }

  return response.json() as Promise<T>;
}

export async function fetchNowTasks(): Promise<NowTasks> {
  const data = await request<{ now: NowTasks }>("/desktop/tasks/now");
  return data.now;
}

export async function fetchToday(): Promise<Today> {
  return request<Today>("/desktop/today");
}

/**
 * Commits minutes of today to a task. Idempotent per (task, day): planning the
 * same task again revises that session rather than adding a second, so the
 * widget needs no separate "edit" call.
 */
export async function planSession(taskId: string, plannedMinutes: number): Promise<void> {
  await request("/desktop/sessions", {
    method: "POST",
    body: JSON.stringify({ taskId, plannedMinutes }),
  });
}

/**
 * Closes a session out. Omitting actualMinutes credits the minutes committed
 * to, which is the common case and must not require typing a number.
 */
export async function completeSession(sessionId: string, actualMinutes?: number): Promise<void> {
  await request(`/desktop/sessions/${encodeURIComponent(sessionId)}/complete`, {
    method: "POST",
    body: JSON.stringify(actualMinutes === undefined ? {} : { actualMinutes }),
  });
}

/**
 * A deliberate pass, which does not count against the month's pace — unlike
 * simply letting the day go by, which leaves the session planned and counts as
 * a miss.
 */
export async function skipSession(sessionId: string): Promise<void> {
  await request(`/desktop/sessions/${encodeURIComponent(sessionId)}/skip`, { method: "POST" });
}

/**
 * Designate a task as a routine — measured in hours per month rather than
 * finished once — or stop measuring it as one.
 *
 * Setting a target also starts the task if it was merely open, since a routine
 * only reaches the ongoing bucket while it is in_progress. Clearing one leaves
 * the task running: no longer measuring something monthly is not the same as
 * no longer doing it.
 */
export async function setRoutineTarget(
  taskId: string,
  monthlyTargetMinutes: number | null,
): Promise<void> {
  await request(`/desktop/tasks/${encodeURIComponent(taskId)}/routine`, {
    method: "POST",
    body: JSON.stringify({ monthlyTargetMinutes }),
  });
}

export async function completeTask(taskId: string): Promise<void> {
  await request(`/desktop/tasks/${encodeURIComponent(taskId)}/complete`, { method: "POST" });
}

export async function snoozeTask(taskId: string, minutes: number): Promise<void> {
  await request(`/desktop/tasks/${encodeURIComponent(taskId)}/snooze`, {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });
}
