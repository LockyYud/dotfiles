import { Action, ActionPanel, Form, Icon, useNavigation } from "@vicinae/api";
import { useState } from "react";
import type { TaskRow } from "./api-client";
import { MONTHLY_TARGET_PRESETS, formatMonthlyTarget, parseHours } from "./format";

/**
 * Typing a monthly target, for the goals a preset does not fit.
 *
 * Takes hours rather than minutes: "20 hours of English a month" is the way
 * the target is actually decided, and 1200 is not a number anyone means.
 */
function TargetForm(props: {
  title: string;
  initialHours: number;
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
            title="Set Monthly Target"
            icon={Icon.Clock}
            onSubmit={async (values: { hours?: string }) => {
              const minutes = parseHours(values.hours ?? "");
              if (minutes === null) {
                setError("Hours per month, e.g. 20");
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
        id="hours"
        title="Hours per month"
        placeholder="20"
        defaultValue={String(props.initialHours)}
        error={error}
      />
    </Form>
  );
}

/**
 * The actions that make a task a routine, or stop measuring one.
 *
 * Shared by both commands because it is the same decision from either view,
 * and it belongs in the widget at all for one reason: designating a routine
 * used to require opening a chat window and saying so in words.
 */
export function RoutineActions(props: {
  task: TaskRow;
  onSet: (minutes: number | null) => Promise<void>;
}) {
  const { push } = useNavigation();
  const current = props.task.pace?.targetMinutes ?? null;
  const isRoutine = current !== null;

  return (
    <ActionPanel.Section title={isRoutine ? "Monthly target" : "Make a routine"}>
      {MONTHLY_TARGET_PRESETS.map((minutes) => (
        <Action
          key={minutes}
          title={
            minutes === current
              ? `${formatMonthlyTarget(minutes)} (current)`
              : formatMonthlyTarget(minutes)
          }
          icon={minutes === current ? Icon.Checkmark : Icon.Clock}
          onAction={() => props.onSet(minutes)}
        />
      ))}
      <Action
        title="Custom…"
        icon={Icon.Pencil}
        onAction={() =>
          push(
            <TargetForm
              title={props.task.title}
              initialHours={current === null ? 20 : current / 60}
              onSubmit={(minutes) => props.onSet(minutes)}
            />,
          )
        }
      />
      {isRoutine && (
        <Action
          title="Stop Measuring Monthly"
          icon={Icon.Minus}
          // The task itself keeps running; only the measurement stops.
          onAction={() => props.onSet(null)}
        />
      )}
    </ActionPanel.Section>
  );
}
