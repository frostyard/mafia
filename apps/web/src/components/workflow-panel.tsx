"use client";

import {
  useAgent,
  useAgentContext,
  useInterrupt,
} from "@copilotkit/react-core/v2";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { resetRunToSpecification } from "@/lib/api";
import type { WorkflowType } from "@/lib/types";
import { startOrRestoreWorkflow } from "@/lib/workflow-control";

interface DecisionProps {
  kind: "artifact" | "phase" | "pull_request_review";
  message: string;
  resolve: (payload?: unknown, interruptId?: string) => Promise<unknown>;
}

function DecisionCard({ kind, message, resolve }: DecisionProps) {
  const [feedback, setFeedback] = useState("");
  const [isResponding, setIsResponding] = useState(false);
  const [error, setError] = useState<string>();

  async function respond(payload: unknown) {
    setError(undefined);
    setIsResponding(true);
    try {
      await resolve(payload);
    } catch {
      setError("The decision could not be sent. Please try again.");
    } finally {
      setIsResponding(false);
    }
  }

  return (
    <section className="decision-card" aria-live="polite">
      <p className="eyebrow">
        {kind === "phase"
          ? "Phase approval"
          : kind === "pull_request_review"
            ? "Review publication"
            : "Artifact approval"}
      </p>
      <h3>
        {kind === "phase"
          ? "Start this phase?"
          : kind === "pull_request_review"
            ? "Publish this review?"
            : "Review this artifact"}
      </h3>
      <p>{message}</p>
      {kind === "artifact" ? (
        <label>
          Refinement feedback
          <textarea
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="Describe what should change before approval."
            rows={3}
            value={feedback}
          />
        </label>
      ) : null}
      <div className="decision-actions">
        {kind === "pull_request_review" ? (
          <>
            <button
              className="button"
              disabled={isResponding}
              onClick={() => respond({ action: "post" })}
              type="button"
            >
              Post to pull request
            </button>
            <button
              className="button button-secondary"
              disabled={isResponding}
              onClick={() => respond({ action: "finish" })}
              type="button"
            >
              Finish without posting
            </button>
          </>
        ) : (
          <button
            className="button"
            disabled={isResponding}
            onClick={() => respond({ action: kind === "phase" ? "start" : "accept" })}
            type="button"
          >
            {kind === "phase" ? "Start phase" : "Accept"}
          </button>
        )}
        {kind === "artifact" ? (
          <button
            className="button button-secondary"
            disabled={isResponding || !feedback.trim()}
            onClick={() => respond({ action: "refine", feedback: feedback.trim() })}
            type="button"
          >
            Refine
          </button>
        ) : null}
        <button
          className="button button-quiet"
          disabled={isResponding}
          onClick={() => respond({ action: "cancel" })}
          type="button"
        >
          Cancel
        </button>
      </div>
      {error ? <p className="form-alert" role="alert">{error}</p> : null}
    </section>
  );
}

function interruptRequestType(metadata: unknown): string | undefined {
  if (typeof metadata !== "object" || metadata === null) return undefined;
  const framework = (metadata as Record<string, unknown>).agent_framework;
  if (typeof framework !== "object" || framework === null) return undefined;
  const requestType = (framework as Record<string, unknown>).request_type;
  return typeof requestType === "string" ? requestType : undefined;
}

function WorkflowDecisions({
  onVisibilityChange,
}: {
  onVisibilityChange: (visible: boolean) => void;
}) {
  const decision = useInterrupt({
    agentId: "mafia",
    renderInChat: false,
    render: ({ interrupt, resolve }) => {
      const message = interrupt?.message ?? "The workflow is waiting for a decision.";
      const requestType = interruptRequestType(interrupt?.metadata);
      const kind =
        requestType === "PhaseDecisionRequest"
          ? "phase"
          : requestType === "PullRequestReviewDecisionRequest"
            ? "pull_request_review"
            : "artifact";
      return (
        <DecisionCard
          kind={kind}
          message={message}
          resolve={resolve}
        />
      );
    },
  });
  useEffect(() => {
    onVisibilityChange(Boolean(decision));
  }, [decision, onVisibilityChange]);
  return decision ?? null;
}

function WorkflowControls({
  activeSpecRevision,
  decisionVisible,
  runId,
  runState,
  threadId,
  workflowType,
}: {
  activeSpecRevision: number | null;
  decisionVisible: boolean;
  runId: string;
  runState: string;
  threadId: string;
  workflowType: WorkflowType;
}) {
  const { agent, isReady } = useAgent({ agentId: "mafia" });
  const router = useRouter();
  const [isStarting, setIsStarting] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isConfirmingReset, setIsConfirmingReset] = useState(false);
  const [error, setError] = useState<string>();

  useAgentContext({
    description: "The MAFIA run attached to this workflow session",
    value: { runId, threadId },
  });

  async function start() {
    setError(undefined);
    setIsStarting(true);
    try {
      await startOrRestoreWorkflow(agent, runId, waitingForDecision);
    } catch {
      setError("The workflow could not start. Confirm that the agent service is running.");
    } finally {
      setIsStarting(false);
    }
  }

  async function resetSpecification() {
    setError(undefined);
    setIsResetting(true);
    try {
      await resetRunToSpecification(runId);
      setIsConfirmingReset(false);
      router.refresh();
    } catch (resetError) {
      setError(
        (resetError as { message?: string }).message ??
          "The workflow could not return to specification refinement.",
      );
    } finally {
      setIsResetting(false);
    }
  }

  const waitingForDecision =
    runState === "awaiting_spec_decision" ||
    runState === "awaiting_plan_decision" ||
    runState === "awaiting_pr_review_decision";
  const canStart = runState === "intake" || runState === "failed";
  const showButton = canStart || (waitingForDecision && !decisionVisible);
  const buttonLabel = waitingForDecision
    ? "Restore decision controls"
    : runState === "failed"
      ? workflowType === "pull_request_review"
        ? "Retry review"
        : "Retry workflow"
      : workflowType === "pull_request_review"
        ? "Start review"
        : "Start workflow";
  const canResetSpecification =
    workflowType === "specification" &&
    activeSpecRevision !== null &&
    !["intake", "generating_spec", "awaiting_spec_decision"].includes(runState);

  return (
    <>
      <div className="workflow-controls">
        <div>
          <p className="eyebrow">Workflow</p>
          <h2 id="workflow-heading">Run controls</h2>
          <p className="muted">Durable thread <code>{threadId}</code></p>
        </div>
        <div className="decision-actions">
          {canResetSpecification ? (
            <button
              className="button button-secondary"
              disabled={isStarting || isResetting}
              onClick={() => {
                setError(undefined);
                setIsConfirmingReset(true);
              }}
              type="button"
            >
              {isResetting ? "Resetting..." : "Adjust specification"}
            </button>
          ) : null}
          {showButton ? (
            <button
              className="button"
              disabled={isStarting || isResetting}
              onClick={start}
              type="button"
            >
              {isStarting ? "Starting..." : buttonLabel}
            </button>
          ) : null}
        </div>
      </div>
      {showButton && !isReady ? (
        <p className="connection-warning">
          The agent connection is not ready. This action will attempt to reconnect it.
        </p>
      ) : null}
      {isConfirmingReset ? (
        <section className="reset-confirmation" role="alert">
          <p>
            Return to specification refinement? Unstarted phases and the active
            plan will be discarded. Merged phases and phases with an open pull
            request will remain recorded.
          </p>
          <div className="decision-actions">
            <button
              className="button"
              disabled={isResetting}
              onClick={resetSpecification}
              type="button"
            >
              {isResetting ? "Resetting..." : "Confirm adjustment"}
            </button>
            <button
              className="button button-quiet"
              disabled={isResetting}
              onClick={() => setIsConfirmingReset(false)}
              type="button"
            >
              Keep current specification
            </button>
          </div>
        </section>
      ) : null}
      {error ? <p className="form-alert" role="alert">{error}</p> : null}
    </>
  );
}

export function WorkflowPanel({
  activeSpecRevision,
  runId,
  runState,
  threadId,
  workflowType,
}: {
  activeSpecRevision: number | null;
  runId: string;
  runState: string;
  threadId: string;
  workflowType: WorkflowType;
}) {
  const [decisionVisible, setDecisionVisible] = useState(false);
  const handleDecisionVisibility = useCallback((visible: boolean) => {
    setDecisionVisible(visible);
  }, []);
  return (
    <section className="workflow-panel ph-card" aria-labelledby="workflow-heading">
      <WorkflowControls
        activeSpecRevision={activeSpecRevision}
        decisionVisible={decisionVisible}
        runId={runId}
        runState={runState}
        threadId={threadId}
        workflowType={workflowType}
      />
      <WorkflowDecisions onVisibilityChange={handleDecisionVisibility} />
    </section>
  );
}
