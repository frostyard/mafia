"use client";

import { useAgent } from "@copilotkit/react-core/v2";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { cancelRun, getRunActivity, prepareRunRetry } from "@/lib/api";
import {
  eventLabel,
  humanizeIdentifier,
  operationLabel,
} from "@/lib/activity-labels";
import { shouldRefreshRunPage } from "@/lib/activity-refresh";
import type { Operation, RunActivity, WorkflowType } from "@/lib/types";

const planSteps = [
  {
    label: "Source grounding",
    operation: "source.grounding",
    matches: (operation: Operation) =>
      typeof operation.detail.reason === "string" &&
      operation.detail.reason.startsWith("plan-"),
  },
  { label: "Plan generation", operation: "model.plan_generation" },
  { label: "Adversarial review", operation: "model.adversarial_review" },
  { label: "Adjudication", operation: "model.plan_adjudication" },
  {
    label: "Artifact persistence",
    operation: "artifact.persistence",
    matches: (operation: Operation) => operation.detail.artifact !== "draft_plan",
  },
] as const;

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainder}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function timeLabel(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function stringValues(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function collectProgress(activity: RunActivity) {
  const files = new Set<string>();
  const commands = new Set<string>();
  for (const operation of activity.operations) {
    for (const source of [operation.detail, operation.result ?? {}]) {
      for (const key of [
        "files_inspected",
        "files_written",
        "changed_files",
        "reported_changed_files",
      ]) {
        stringValues(source[key]).forEach((item) => files.add(item));
      }
    }
    if (
      (operation.operation_type === "environment.validation" ||
        operation.operation_type === "sandbox.validation") &&
      typeof operation.detail.command === "string"
    ) {
      commands.add(operation.detail.command);
    }
  }
  return {
    commands: [...commands].slice(0, 12),
    files: [...files].slice(0, 20),
  };
}

const completedPlanStates = new Set([
  "awaiting_plan_decision",
  "ready_for_phase",
  "executing_phase",
  "pr_open",
  "waiting_for_merge",
  "completed",
]);

const activePlanState: Record<string, string> = {
  grounding_plan: "source.grounding",
  regrounding: "source.grounding",
  generating_plan: "model.plan_generation",
  reviewing_plan: "model.adversarial_review",
  adjudicating_plan: "model.plan_adjudication",
  persisting_plan: "artifact.persistence",
};

function PlanProgress({
  operations,
  state,
}: {
  operations: Operation[];
  state: string;
}) {
  return (
    <ol className="substep-list">
      {planSteps.map((step) => {
        const matching = operations.filter(
          (operation) =>
            operation.operation_type === step.operation &&
            (!("matches" in step) || step.matches(operation)),
        );
        const active =
          matching.some((operation) => operation.status === "running") ||
          activePlanState[state] === step.operation;
        const complete =
          completedPlanStates.has(state) ||
          matching.some((operation) => operation.status === "completed");
        const stepState = active ? "active" : complete ? "complete" : "pending";
        return (
          <li className={`substep substep-${stepState}`} key={step.label}>
            <span className="substep-marker" aria-hidden="true">
              {complete ? "ok" : active ? "*" : ""}
            </span>
            <span>{step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

const completedPullRequestReviewStates = new Set([
  "awaiting_pr_review_decision",
  "posting_pr_review",
  "completed",
]);

const pullRequestReviewSteps = [
  {
    label: "Pull request grounding",
    operation: "source.grounding",
    matches: (operation: Operation) =>
      operation.detail.reason === "pull_request_review",
  },
  { label: "Independent model reviews", operation: "model.pull_request_review" },
  {
    label: "Adjudication",
    operation: "model.pull_request_review_adjudication",
  },
  {
    label: "Consolidated artifact",
    operation: "artifact.persistence",
    matches: (operation: Operation) =>
      operation.detail.artifact === "pull_request_review_consolidated",
  },
] as const;

function PullRequestReviewProgress({
  operations,
  state,
}: {
  operations: Operation[];
  state: string;
}) {
  const activeOperation: Record<string, string> = {
    grounding_pr_review: "source.grounding",
    reviewing_pr: "model.pull_request_review",
    consolidating_pr_review: "model.pull_request_review_adjudication",
  };
  return (
    <ol className="substep-list">
      {pullRequestReviewSteps.map((step) => {
        const matching = operations.filter(
          (operation) =>
            operation.operation_type === step.operation &&
            (!("matches" in step) || step.matches(operation)),
        );
        const active =
          matching.some((operation) => operation.status === "running") ||
          activeOperation[state] === step.operation;
        const requiredCompletions =
          step.operation === "model.pull_request_review" ? 2 : 1;
        const complete =
          completedPullRequestReviewStates.has(state) ||
          matching.filter((operation) => operation.status === "completed").length >=
            requiredCompletions;
        const stepState = active ? "active" : complete ? "complete" : "pending";
        return (
          <li className={`substep substep-${stepState}`} key={step.label}>
            <span className="substep-marker" aria-hidden="true">
              {complete ? "ok" : active ? "*" : ""}
            </span>
            <span>{step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

function OperationList({ operations }: { operations: Operation[] }) {
  if (operations.length === 0) {
    return <p className="rail-placeholder">No recorded operations yet.</p>;
  }
  return (
    <ul className="operation-list">
      {operations.slice(0, 10).map((operation) => (
        <li key={operation.id}>
          <div className="operation-heading">
            <strong>{operationLabel(operation.operation_type)}</strong>
            <span className={`operation-status operation-${operation.status}`}>
              {humanizeIdentifier(operation.status)}
            </span>
          </div>
          <div className="operation-meta">
            {operation.model ? <span>{operation.model}</span> : null}
            <span>{formatElapsed(operation.elapsed_seconds)}</span>
            <span>attempt {operation.attempt}</span>
          </div>
          <p>
            Started {timeLabel(operation.started_at)} / heartbeat{" "}
            {timeLabel(operation.heartbeat_at)} / progress {timeLabel(operation.progress_at)}
            {operation.timeout_seconds ? ` / timeout ${operation.timeout_seconds}s` : ""}
          </p>
          {operation.error?.message ? (
            <p className="operation-error">{String(operation.error.message)}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function VisibilityRail({
  initialActivity,
  runId,
  workflowType,
}: {
  initialActivity: RunActivity;
  runId: string;
  workflowType: WorkflowType;
}) {
  const [activity, setActivity] = useState(initialActivity);
  const [controlError, setControlError] = useState<string>();
  const [pollError, setPollError] = useState<string>();
  const [isControlling, setIsControlling] = useState(false);
  const activityRef = useRef(initialActivity);
  const router = useRouter();
  const { agent, isReady } = useAgent({ agentId: "mafia" });

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await getRunActivity(runId);
        if (disposed) return;
        setPollError(undefined);
        if (shouldRefreshRunPage(activityRef.current, next)) {
          router.refresh();
        }
        activityRef.current = next;
        setActivity(next);
      } catch {
        if (!disposed) {
          setPollError("Live updates are paused while the API is unavailable.");
        }
      } finally {
        if (!disposed) timer = setTimeout(poll, 3_000);
      }
    }
    timer = setTimeout(poll, 3_000);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [router, runId]);

  async function cancel() {
    setControlError(undefined);
    setIsControlling(true);
    try {
      setActivity(await cancelRun(runId));
      router.refresh();
    } catch (error) {
      setControlError((error as { message?: string }).message ?? "Cancellation failed.");
    } finally {
      setIsControlling(false);
    }
  }

  async function retry() {
    setControlError(undefined);
    setIsControlling(true);
    try {
      setActivity(await prepareRunRetry(runId));
      agent.addMessage({
        id: crypto.randomUUID(),
        role: "user",
        content: `Retry workflow run ${runId}.`,
      });
      await agent.runAgent();
    } catch (error) {
      setControlError((error as { message?: string }).message ?? "Retry failed.");
    } finally {
      setIsControlling(false);
      router.refresh();
    }
  }

  const progress = collectProgress(activity);

  return (
    <aside className="visibility-rail" aria-label="Workflow activity">
      <section className={`status-banner status-${activity.status_mode}`} aria-live="polite">
        <p className="eyebrow">Workflow status</p>
        <h2>{activity.status_mode === "decision" ? "Input required" : activity.status_mode}</h2>
        <p>{activity.status_message}</p>
        {activity.stalled ? (
          <p className="stall-warning">
            {activity.stall_reason ?? "The active operation appears stalled"} (
            {Math.floor(activity.stall_threshold_seconds / 60)} minute threshold).
          </p>
        ) : null}
        <div className="rail-actions">
          {activity.can_cancel ? (
            <button
              className="button button-small button-secondary"
              disabled={isControlling}
              onClick={cancel}
              type="button"
            >
              Cancel work
            </button>
          ) : null}
          {activity.can_retry ? (
            <button
              className="button button-small"
              disabled={isControlling || !isReady}
              onClick={retry}
              type="button"
            >
              Retry
            </button>
          ) : null}
        </div>
        {controlError ? <p className="operation-error">{controlError}</p> : null}
        {pollError ? <p className="operation-error">{pollError}</p> : null}
      </section>

      <section className="rail-section">
        <h2>
          {workflowType === "pull_request_review"
            ? "Review steps"
            : "Planning steps"}
        </h2>
        {workflowType === "pull_request_review" ? (
          <PullRequestReviewProgress
            operations={activity.operations}
            state={activity.state}
          />
        ) : (
          <PlanProgress operations={activity.operations} state={activity.state} />
        )}
      </section>

      <section className="rail-section">
        <h2>Progress details</h2>
        <dl className="rail-metrics">
          <div><dt>Source SHA</dt><dd><code>{activity.source_sha?.slice(0, 12) ?? "pending"}</code></dd></div>
          <div><dt>Files discovered</dt><dd>{activity.files_discovered ?? "pending"}</dd></div>
          <div><dt>Citations</dt><dd>{activity.citations_found}</dd></div>
        </dl>
        {progress.files.length > 0 ? (
          <details>
            <summary>Files inspected or changed ({progress.files.length})</summary>
            <ul className="compact-list">
              {progress.files.map((file) => <li key={file}><code>{file}</code></li>)}
            </ul>
          </details>
        ) : null}
        {progress.commands.length > 0 ? (
          <details>
            <summary>Validation commands ({progress.commands.length})</summary>
            <ul className="compact-list">
              {progress.commands.map((command) => <li key={command}><code>{command}</code></li>)}
            </ul>
          </details>
        ) : null}
      </section>

      <section className="rail-section">
        <h2>Live operations</h2>
        <OperationList operations={activity.operations} />
      </section>

      <section className="rail-section">
        <h2>Activity timeline</h2>
        {activity.events.length === 0 ? (
          <p className="rail-placeholder">No durable events yet.</p>
        ) : (
          <ol className="activity-list">
            {activity.events.slice(0, 16).map((event) => (
              <li key={event.id}>
                <time dateTime={event.created_at}>{timeLabel(event.created_at)}</time>
                <span>{eventLabel(event.event_type)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </aside>
  );
}
