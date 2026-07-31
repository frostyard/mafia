"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { resetRunToSpecification, startRun, submitDecision } from "@/lib/api";
import type { DecisionPayload, PendingAction, RunDetail } from "@/lib/types";

const DEFAULT_PROMPT = "The workflow is waiting for a decision.";

function payloadString(payload: Record<string, unknown>, key: string): string | undefined {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : undefined;
}

function actionTitle(kind: PendingAction["kind"]): string {
  switch (kind) {
    case "phase":
      return "Start this phase?";
    case "pull_request_review":
      return "Publish this review?";
    case "configuration_required":
      return "Configuration required";
    default:
      return "Review this artifact";
  }
}

function actionEyebrow(kind: PendingAction["kind"]): string {
  switch (kind) {
    case "phase":
      return "Phase approval";
    case "pull_request_review":
      return "Review publication";
    case "configuration_required":
      return "Project setup";
    default:
      return "Artifact approval";
  }
}

function DecisionCard({ action, runId, projectId }: { action: PendingAction; runId: string; projectId: string }) {
  const router = useRouter();
  const [feedback, setFeedback] = useState("");
  const [isResponding, setIsResponding] = useState(false);
  const [error, setError] = useState<string>();
  const message = payloadString(action.payload, "message") ?? payloadString(action.payload, "prompt") ?? DEFAULT_PROMPT;
  const configuredProjectId = payloadString(action.payload, "project_id") ?? projectId;

  async function respond(payload: DecisionPayload) {
    setError(undefined);
    setIsResponding(true);
    try {
      await submitDecision(runId, action.id, payload);
      router.refresh();
    } catch (submissionError) {
      setError((submissionError as { message?: string }).message ?? "The decision could not be sent. Please try again.");
    } finally {
      setIsResponding(false);
    }
  }

  const isArtifact = action.kind === "specification" || action.kind === "plan";

  return (
    <section className="decision-card" aria-live="polite">
      <p className="eyebrow">{actionEyebrow(action.kind)}</p>
      <h3>{actionTitle(action.kind)}</h3>
      <p>{message}</p>
      {isArtifact ? (
        <label>
          Refinement feedback
          <textarea onChange={(event) => setFeedback(event.target.value)} placeholder="Describe what should change before approval." rows={3} value={feedback} />
        </label>
      ) : null}
      <div className="decision-actions">
        {action.kind === "pull_request_review" ? (
          <>
            <button className="button" disabled={isResponding} onClick={() => respond({ action: "post" })} type="button">Post to pull request</button>
            <button className="button button-secondary" disabled={isResponding} onClick={() => respond({ action: "finish" })} type="button">Finish without posting</button>
          </>
        ) : action.kind === "configuration_required" ? (
          <>
            <Link className="button button-secondary" href={`/projects/${configuredProjectId}`}>Open project settings</Link>
            <button className="button" disabled={isResponding} onClick={() => respond({ action: "check_again" })} type="button">Check again</button>
          </>
        ) : (
          <button className="button" disabled={isResponding} onClick={() => respond({ action: action.kind === "phase" ? "start" : "accept" })} type="button">
            {isResponding ? "Starting..." : action.kind === "phase" ? "Start phase" : "Accept"}
          </button>
        )}
        {isArtifact ? (
          <button className="button button-secondary" disabled={isResponding || !feedback.trim()} onClick={() => respond({ action: "refine", feedback: feedback.trim() })} type="button">Refine</button>
        ) : null}
        {action.kind !== "configuration_required" ? (
          <button className="button button-quiet" disabled={isResponding} onClick={() => respond({ action: "cancel" })} type="button">Cancel</button>
        ) : null}
      </div>
      {error ? <p className="form-alert" role="alert">{error}</p> : null}
    </section>
  );
}

export function WorkflowPanel({ run }: { run: RunDetail }) {
  const router = useRouter();
  const [isStarting, setIsStarting] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isConfirmingReset, setIsConfirmingReset] = useState(false);
  const [error, setError] = useState<string>();
  const canResetSpecification = run.workflow_type === "specification" && run.active_spec_revision !== null && !["intake", "generating_spec", "awaiting_spec_decision"].includes(run.state);

  async function start() {
    setError(undefined);
    setIsStarting(true);
    try {
      await startRun(run.id);
      router.refresh();
    } catch (startError) {
      setError((startError as { message?: string }).message ?? "The workflow could not start. Please try again.");
    } finally {
      setIsStarting(false);
    }
  }

  async function resetSpecification() {
    setError(undefined);
    setIsResetting(true);
    try {
      await resetRunToSpecification(run.id);
      setIsConfirmingReset(false);
      router.refresh();
    } catch (resetError) {
      setError((resetError as { message?: string }).message ?? "The workflow could not return to specification refinement.");
    } finally {
      setIsResetting(false);
    }
  }

  return (
    <section className="workflow-panel ph-card" aria-labelledby="workflow-heading">
      <div className="workflow-controls">
        <div>
          <p className="eyebrow">Workflow</p>
          <h2 id="workflow-heading">Run controls</h2>
        </div>
        <div className="decision-actions">
          {canResetSpecification ? <button className="button button-secondary" disabled={isStarting || isResetting} onClick={() => { setError(undefined); setIsConfirmingReset(true); }} type="button">{isResetting ? "Resetting..." : "Adjust specification"}</button> : null}
          {run.state === "intake" ? <button className="button" disabled={isStarting || isResetting} onClick={start} type="button">{isStarting ? "Starting..." : "Start workflow"}</button> : null}
        </div>
      </div>
      {isConfirmingReset ? (
        <section className="reset-confirmation" role="alert">
          <p>Return to specification refinement? Unstarted phases and the active plan will be discarded. Merged phases and phases with an open pull request will remain recorded.</p>
          <div className="decision-actions">
            <button className="button" disabled={isResetting} onClick={resetSpecification} type="button">{isResetting ? "Resetting..." : "Confirm adjustment"}</button>
            <button className="button button-quiet" disabled={isResetting} onClick={() => setIsConfirmingReset(false)} type="button">Keep current specification</button>
          </div>
        </section>
      ) : null}
      {error ? <p className="form-alert" role="alert">{error}</p> : null}
      {run.pending_action ? <DecisionCard action={run.pending_action} projectId={run.repository.id} runId={run.id} /> : null}
    </section>
  );
}
