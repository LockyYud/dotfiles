import { Action, ActionPanel, Icon, List, Toast, showToast } from "@vicinae/api";
import { useEffect, useState } from "react";
import {
  APP_URL,
  completeTask,
  fetchNowTasks,
  planSession,
  setRoutineTarget,
  snoozeTask,
  type NowTasks,
  type TaskRow,
} from "./lib/api-client";
import { describePace, formatMinutes } from "./lib/format";
import { RoutineActions } from "./lib/routine-actions";

const TASKS_URL = `${APP_URL}/tasks`;

export default function Command() {
  const [now, setNow] = useState<NowTasks | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setIsLoading(true);
    try {
      setNow(await fetchNowTasks());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load tasks");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleComplete(task: TaskRow) {
    try {
      await completeTask(task.id);
      await showToast({ style: Toast.Style.Success, title: `Completed "${task.title}"` });
      await load();
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Couldn't complete task",
        message: err instanceof Error ? err.message : undefined,
      });
    }
  }

  async function handleSnooze(task: TaskRow, minutes: number) {
    try {
      await snoozeTask(task.id, minutes);
      await showToast({ style: Toast.Style.Success, title: `Snoozed "${task.title}"` });
      await load();
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Couldn't snooze task",
        message: err instanceof Error ? err.message : undefined,
      });
    }
  }

  if (error) {
    return (
      <List>
        <List.EmptyView title="Couldn't reach Persona Assistant" description={error} icon={Icon.Warning} />
      </List>
    );
  }

  async function handlePlan(task: TaskRow, minutes: number) {
    try {
      await planSession(task.id, minutes);
      await showToast({
        style: Toast.Style.Success,
        title: `${task.title}: ${formatMinutes(minutes)} today`,
      });
      await load();
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Couldn't plan the session",
        message: err instanceof Error ? err.message : undefined,
      });
    }
  }

  async function handleSetRoutine(task: TaskRow, minutes: number | null) {
    try {
      await setRoutineTarget(task.id, minutes);
      await showToast({
        style: Toast.Style.Success,
        title:
          minutes === null
            ? `${task.title} is no longer measured monthly`
            : `${task.title}: ${formatMinutes(minutes)} a month`,
      });
      await load();
    } catch (err) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Couldn't change the monthly target",
        message: err instanceof Error ? err.message : undefined,
      });
    }
  }

  const sections: Array<{ title: string; items: TaskRow[] }> = now
    ? [
        { title: "Overdue", items: now.overdue },
        { title: "Today", items: now.today },
        { title: "Next up", items: now.nextUp ? [now.nextUp] : [] },
      ]
    : [];
  // Routines carry no dueAt, so they appear in none of the buckets above.
  // Listing them here is not decoration: without it this view would report
  // "all clear" while a routine sat weeks behind its month. Planning the day
  // properly lives in the Plan Today command; one action for the suggested
  // amount is enough from here.
  const routines = (now?.ongoing ?? []).filter((task) => task.pace);
  const hasAny = sections.some((section) => section.items.length > 0) || routines.length > 0;

  return (
    <List isLoading={isLoading}>
      {sections.map(
        (section) =>
          section.items.length > 0 && (
            <List.Section key={section.title} title={section.title}>
              {section.items.map((task) => (
                <List.Item
                  key={task.id}
                  title={task.title}
                  subtitle={task.priority}
                  accessories={task.dueAt ? [{ text: new Date(task.dueAt).toLocaleString() }] : []}
                  actions={
                    <ActionPanel>
                      <Action title="Complete" icon={Icon.Checkmark} onAction={() => handleComplete(task)} />
                      <Action.OpenInBrowser title="Open in Persona Assistant" url={TASKS_URL} />
                      <RoutineActions
                        task={task}
                        onSet={(minutes) => handleSetRoutine(task, minutes)}
                      />
                      <ActionPanel.Section title="Snooze">
                        <Action title="Snooze 1 Hour" icon={Icon.Clock} onAction={() => handleSnooze(task, 60)} />
                        <Action title="Snooze 3 Hours" icon={Icon.Clock} onAction={() => handleSnooze(task, 180)} />
                        <Action
                          title="Snooze Until Tomorrow"
                          icon={Icon.Calendar}
                          onAction={() => handleSnooze(task, 24 * 60)}
                        />
                      </ActionPanel.Section>
                    </ActionPanel>
                  }
                />
              ))}
            </List.Section>
          ),
      )}
      {routines.length > 0 && (
        <List.Section title="Routines" subtitle={`${routines.length}`}>
          {routines.map((task) => {
            const suggested = task.pace?.suggestedTodayMinutes ?? 0;
            return (
              <List.Item
                key={task.id}
                title={task.title}
                subtitle={task.pace ? describePace(task.pace) : undefined}
                icon={task.pace?.status === "behind" ? Icon.Warning : Icon.Circle}
                accessories={[
                  { text: suggested > 0 ? `suggest ${formatMinutes(suggested)}` : "target met" },
                ]}
                actions={
                  <ActionPanel>
                    {suggested > 0 && (
                      <Action
                        title={`Plan ${formatMinutes(suggested)} Today`}
                        icon={Icon.Clock}
                        onAction={() => handlePlan(task, suggested)}
                      />
                    )}
                    <RoutineActions
                      task={task}
                      onSet={(minutes) => handleSetRoutine(task, minutes)}
                    />
                    <Action.OpenInBrowser title="Open in Persona Assistant" url={TASKS_URL} />
                  </ActionPanel>
                }
              />
            );
          })}
        </List.Section>
      )}
      {!isLoading && !hasAny && !error && (
        <List.EmptyView
          title="All clear"
          description={
            now && now.unscheduledCount > 0
              ? `No overdue or due-today tasks. ${now.unscheduledCount} open task${now.unscheduledCount === 1 ? "" : "s"} with no due date.`
              : "No overdue or due-today tasks."
          }
          icon={Icon.Checkmark}
        />
      )}
    </List>
  );
}
