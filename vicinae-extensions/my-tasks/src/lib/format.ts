import type { Pace } from "./api-client";

/**
 * Compact durations: "1h45m", "45m", "2h".
 *
 * Deliberately the same shapes the worker's own formatMinutes produces, since
 * the widget, the morning briefing and the chat agent all describe the same
 * numbers and reading them differently in each place is its own small tax.
 */
export function formatMinutes(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (hours === 0) return `${rest}m`;
  if (rest === 0) return `${hours}h`;
  return `${hours}h${rest}m`;
}

/**
 * Where a routine's month stands, leading with the gap rather than the totals
 * — that is the part that decides whether today needs more time than planned.
 */
export function describePace(pace: Pace): string {
  const spent = `${formatMinutes(pace.spentMinutes)} of ${formatMinutes(pace.targetMinutes)}`;
  const gap = formatMinutes(Math.abs(pace.deltaMinutes));

  if (pace.status === "behind") return `${spent} · behind by ${gap}`;
  if (pace.status === "ahead") return `${spent} · ahead by ${gap}`;
  return `${spent} · on track`;
}

/** Durations offered without typing. Anything else goes through the form. */
export const DURATION_PRESETS = [15, 30, 45, 60, 90, 120];

/**
 * Parses a typed duration: "90", "1h30", "1h30m", "1h", "45m".
 *
 * Returns null rather than throwing or guessing, so the form can say what is
 * wrong instead of silently committing a number the user did not mean.
 */
export function parseDuration(raw: string): number | null {
  const text = raw.trim().toLowerCase();
  if (!text) return null;

  // Bare number means minutes, which is what someone typing "90" intends.
  if (/^\d+$/.test(text)) {
    const minutes = Number(text);
    return minutes > 0 && minutes <= 24 * 60 ? minutes : null;
  }

  const match = /^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m?)?$/.exec(text);
  if (!match || (!match[1] && !match[2])) return null;

  const minutes = Number(match[1] ?? 0) * 60 + Number(match[2] ?? 0);
  return minutes > 0 && minutes <= 24 * 60 ? minutes : null;
}

/** Monthly targets offered without typing, in minutes. */
export const MONTHLY_TARGET_PRESETS = [5 * 60, 10 * 60, 20 * 60, 40 * 60];

/**
 * Parses a monthly target typed as hours: "20", "20h", "20.5".
 *
 * Separate from parseDuration because the unit and the sane range differ — a
 * session is minutes and caps at a day, a monthly target is hours and caps at
 * a month — and reading "20" as twenty minutes a month would be a silently
 * useless target rather than an obvious mistake.
 */
export function parseHours(raw: string): number | null {
  const text = raw.trim().toLowerCase().replace(/h(ours?)?$/, "").trim();
  if (!/^\d+(\.\d+)?$/.test(text)) return null;

  const hours = Number(text);
  if (hours <= 0 || hours > 31 * 24) return null;
  return Math.round(hours * 60);
}

/** "20h/month", "20.5h/month" — how a target reads in a menu. */
export function formatMonthlyTarget(minutes: number): string {
  const hours = minutes / 60;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h/month`;
}
