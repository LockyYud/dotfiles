import { Action, ActionPanel, Form, Icon, List, Toast, showToast, useNavigation } from "@vicinae/api";
import { useEffect, useState } from "react";
import {
  APP_URL,
  completeSession,
  fetchToday,
  planSession,
  setRoutineTarget,
  skipSession,
  type SessionRow,
  type TaskRow,
  type Today,
} from "./lib/api-client";
import { DURATION_PRESETS, describePace, formatMinutes, parseDuration } from "./lib/format";
import { RoutineActions } from "./lib/routine-actions";

const TASKS_URL = `${APP_URL}/tasks`;

/**
 * Typing a duration, for the times a preset does not fit.
 *
 * Kept as a pushed form rather than inline text parsing on the list's search
 * bar: the search bar filters, and quietly overloading it to also mean "90
 * minutes" would make the list's most-used behaviour ambiguous.
 */
function DurationForm(props: {
  title: string;
  initial: number;
  submitTitle: string;
  onSubmit: (minutes: number) => Promise<void>;
}) {
  const { pop } = useNavigation();
  const [error, setError] = useState<string | undefined>();

  return (
    <Form
      navigationTitle={props.title}
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title={props.submitTitle}
            icon={Icon.Clock}
            onSubmit={async (values: { duration?: string }) => {
              const minutes = parseDuration(values.duration ?? "");
              if (minutes === null) {
                setError("Try 90, 1h30 or 45m");
                return;
              }
              setError(undefined);
              await props.onSubmit(minutes);
              pop();
            }}
          />
        </ActionPanel>
      }
    >
      <Form.TextField
        id="duration"
        title="Duration"
        placeholder="90, 1h30, 45m"
        defaultValue={String(props.initial)}
        error={error}
      />
    </Form>
  );
}

/**
 * The day planner: what today is committed to, and what the month still wants.
 *
 * Both halves are shown because a routine's suggested minutes are only useful
 * next to what has already been promised — "1h planned, 1h45m suggested" is
 * the decision; either number alone is not.
 */
export default function Command() {
  const [today, setToday] = useState<Today | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { push } = useNavigation();

  async function load() {
    setIsLoading(true);
    try {
      setToday(await fetchToday());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load today");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function run(action: () => Promise<void>, success: string, failure: string) {
    try {
      await action();
      await showToast({ style: Toast.Style.Success, title: success });
      await load();
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: failure,
        message: err instanceof Error ? err.message : undefined,
      });
    }
  }

  if (error) {
    return (
      <List>
        <List.EmptyView
          title="Couldn't reach Persona Assistant"
          description={error}
          icon={Icon.Warning}
        />
      </List>
    );
  }

  const sessions = today?.sessions ?? [];
  const routines = today?.ongoing ?? [];
  const plannedByTask = new Map(sessions.map((session) => [session.taskId, session]));

  /** Minutes still owed today: what is suggested, less what is already promised. */
  function remainingFor(task: TaskRow): number {
    const suggested = task.pace?.suggestedTodayMinutes ?? 0;
    const planned = plannedByTask.get(task.id);
    if (!planned || planned.status === "skipped") return suggested;
    return Math.max(0, suggested - planned.plannedMinutes);
  }

  function routineActions(task: TaskRow) {
    return (
      <RoutineActions
        task={task}
        onSet={(minutes) =>
          run(
            () => setRoutineTarget(task.id, minutes),
            minutes === null
              ? `${task.title} is no longer measured monthly`
              : `${task.title}: ${formatMinutes(minutes)} a month`,
            "Couldn't change the monthly target",
          )
        }
      />
    );
  }

  function planActions(task: TaskRow) {
    const remaining = remainingFor(task);
    const suggested = task.pace?.suggestedTodayMinutes ?? 0;
    const existing = plannedByTask.get(task.id);
    // Re-planning replaces the commitment rather than adding to it, so the
    // headline offer has to be the total to end up at, not the shortfall.
    const headline = existing ? existing.plannedMinutes + remaining : suggested;

    return (
      <>
        {headline > 0 && (
          <Action
            title={`${existing ? "Make It" : "Plan"} ${formatMinutes(headline)} Today`}
            icon={Icon.Clock}
            onAction={() =>
              run(
                () => planSession(task.id, headline),
                `${task.title}: ${formatMinutes(headline)} today`,
                "Couldn't plan the session",
              )
            }
          />
        )}
        <ActionPanel.Section title={existing ? "Change to" : "Plan"}>
          {DURATION_PRESETS.map((minutes) => {
            // Marked against what today is actually set to, never against the
            // suggestion: a tick beside a number that was merely recommended
            // would claim it had been chosen.
            const isCurrent =
              existing !== undefined &&
              existing.status !== "skipped" &&
              existing.plannedMinutes === minutes;
            return (
              <Action
                key={minutes}
                title={isCurrent ? `${formatMinutes(minutes)} (current)` : formatMinutes(minutes)}
                icon={isCurrent ? Icon.Checkmark : Icon.Clock}
                onAction={() =>
                  run(
                    () => planSession(task.id, minutes),
                    `${task.title}: ${formatMinutes(minutes)} today`,
                    "Couldn't plan the session",
                  )
                }
              />
            );
          })}
          <Action
            title="Custom…"
            icon={Icon.Pencil}
            onAction={() =>
              push(
                <DurationForm
                  title={task.title}
                  initial={headline > 0 ? headline : 60}
                  submitTitle="Plan Today"
                  onSubmit={(minutes) =>
                    run(
                      () => planSession(task.id, minutes),
                      `${task.title}: ${formatMinutes(minutes)} today`,
                      "Couldn't plan the session",
                    )
                  }
                />,
              )
            }
          />
        </ActionPanel.Section>
      </>
    );
  }

  function sessionSubtitle(session: SessionRow): string {
    if (session.status === "skipped") return "skipped";
    if (session.status === "done") {
      const actual = session.actualMinutes ?? session.plannedMinutes;
      // Only worth spelling out the difference when there is one.
      return actual === session.plannedMinutes
        ? `${formatMinutes(actual)} done`
        : `${formatMinutes(actual)} done of ${formatMinutes(session.plannedMinutes)}`;
    }
    return `${formatMinutes(session.plannedMinutes)} planned`;
  }

  const hasAny = sessions.length > 0 || routines.length > 0;

  return (
    <List isLoading={isLoading} navigationTitle={today ? `Today · ${today.date}` : "Today"}>
      {sessions.length > 0 && (
        <List.Section title="Today" subtitle={`${sessions.length}`}>
          {sessions.map((session) => (
            <List.Item
              key={session.id}
              title={session.task.title}
              subtitle={sessionSubtitle(session)}
              icon={
                session.status === "done"
                  ? Icon.Checkmark
                  : session.status === "skipped"
                    ? Icon.Minus
                    : Icon.Circle
              }
              accessories={
                session.startAt
                  ? [
                      {
                        text: new Date(session.startAt).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        }),
                      },
                    ]
                  : []
              }
              actions={
                <ActionPanel>
                  {session.status === "planned" && (
                    <>
                      <Action
                        title={`Done (${formatMinutes(session.plannedMinutes)})`}
                        icon={Icon.Checkmark}
                        onAction={() =>
                          run(
                            () => completeSession(session.id),
                            `Done: ${session.task.title}`,
                            "Couldn't complete the session",
                          )
                        }
                      />
                      <Action
                        title="Done, But Shorter…"
                        icon={Icon.Pencil}
                        onAction={() =>
                          push(
                            <DurationForm
                              title={session.task.title}
                              initial={session.plannedMinutes}
                              submitTitle="Record"
                              onSubmit={(minutes) =>
                                run(
                                  () => completeSession(session.id, minutes),
                                  `Done: ${session.task.title} (${formatMinutes(minutes)})`,
                                  "Couldn't complete the session",
                                )
                              }
                            />,
                          )
                        }
                      />
                      <Action
                        title="Skip Today"
                        icon={Icon.Minus}
                        onAction={() =>
                          run(
                            () => skipSession(session.id),
                            `Skipped: ${session.task.title}`,
                            "Couldn't skip the session",
                          )
                        }
                      />
                    </>
                  )}
                  <ActionPanel.Section title="Reschedule">
                    {planActions(session.task)}
                  </ActionPanel.Section>
                  {routineActions(session.task)}
                  <Action.OpenInBrowser title="Open in Persona Assistant" url={TASKS_URL} />
                </ActionPanel>
              }
            />
          ))}
        </List.Section>
      )}

      {routines.length > 0 && (
        <List.Section title="Routines" subtitle={`${routines.length}`}>
          {routines.map((task) => {
            const planned = plannedByTask.get(task.id);
            const suggested = task.pace?.suggestedTodayMinutes ?? 0;

            return (
              <List.Item
                key={task.id}
                title={task.title}
                subtitle={task.pace ? describePace(task.pace) : undefined}
                icon={task.pace?.status === "behind" ? Icon.Warning : Icon.Circle}
                accessories={[
                  {
                    text: planned
                      ? `${formatMinutes(planned.plannedMinutes)} planned`
                      : suggested > 0
                        ? `suggest ${formatMinutes(suggested)}`
                        : "target met",
                  },
                ]}
                actions={
                  <ActionPanel>
                    {planActions(task)}
                    {routineActions(task)}
                    <Action.OpenInBrowser title="Open in Persona Assistant" url={TASKS_URL} />
                  </ActionPanel>
                }
              />
            );
          })}
        </List.Section>
      )}

      {!isLoading && !hasAny && (
        <List.EmptyView
          title="Nothing to plan"
          description="No routines yet. Give a task a monthly target — say '20 hours of English a month' in chat — and it will show up here."
          icon={Icon.Calendar}
        />
      )}
    </List>
  );
}
