import { describe, expect, it } from "vitest";
import { shouldRefreshRunPage } from "./activity-refresh";
import type { Operation, RunActivity } from "@/lib/types";

function pendingAction(
  overrides: Partial<NonNullable<RunActivity["pending_action"]>> = {},
): NonNullable<RunActivity["pending_action"]> {
  return {
    id: "action-1",
    kind: "plan",
    expected_run_version: 8,
    artifact_id: "artifact-1",
    phase_id: "phase-1",
    revision: 1,
    payload: {},
    created_at: "2026-07-30T00:00:00Z",
    updated_at: "2026-07-30T00:00:00Z",
    ...overrides,
  };
}

function activity(
  state: RunActivity["state"],
  statusMode: RunActivity["status_mode"],
  version: number,
  pending_action: RunActivity["pending_action"] = null,
): RunActivity {
  return {
    run_id: "run-1",
    state,
    version,
    status_mode: statusMode,
    status_message: "status",
    stalled: false,
    stall_reason: null,
    stall_threshold_seconds: 300,
    can_cancel: false,
    can_retry: false,
    source_sha: null,
    files_discovered: null,
    citations_found: 0,
    pending_action,
    operations: [],
    events: [],
  };
}

function artifactOperation(status: Operation["status"]): Operation {
  return {
    id: "operation-1",
    phase_id: null,
    operation_type: "artifact.persistence",
    status,
    model: null,
    attempt: 1,
    timeout_seconds: null,
    detail: { artifact: "adversarial_review" },
    result: null,
    error: null,
    created_at: "2026-07-30T00:00:00Z",
    updated_at: "2026-07-30T00:00:01Z",
    started_at: "2026-07-30T00:00:00Z",
    heartbeat_at: "2026-07-30T00:00:01Z",
    progress_at: "2026-07-30T00:00:01Z",
    completed_at: status === "completed" ? "2026-07-30T00:00:01Z" : null,
    elapsed_seconds: 1,
  };
}

describe("shouldRefreshRunPage", () => {
  it("refreshes when a working workflow changes structurally", () => {
    const previous = activity("generating_plan", "working", 4);
    const next = activity("reviewing_plan", "working", 5);

    expect(shouldRefreshRunPage(previous, next)).toBe(true);
  });

  it("refreshes when work reaches a durable decision", () => {
    const previous = activity("persisting_plan", "working", 7);
    const next = activity("awaiting_plan_decision", "decision", 8);

    expect(shouldRefreshRunPage(previous, next)).toBe(true);
  });

  it("refreshes when an artifact finishes persisting during active work", () => {
    const previous = activity("reviewing_plan", "working", 5);
    previous.operations = [artifactOperation("running")];
    const next = activity("adjudicating_plan", "working", 6);
    next.operations = [artifactOperation("completed")];

    expect(shouldRefreshRunPage(previous, next)).toBe(true);
  });

  it("refreshes when work structurally changes after an artifact completes", () => {
    const previous = activity("adjudicating_plan", "working", 6);
    previous.operations = [artifactOperation("completed")];
    const next = activity("persisting_plan", "working", 7);
    next.operations = [artifactOperation("completed")];

    expect(shouldRefreshRunPage(previous, next)).toBe(true);
  });

  it("does not refresh unchanged projections", () => {
    const previous = activity("awaiting_plan_decision", "decision", 8);

    expect(shouldRefreshRunPage(previous, previous)).toBe(false);
  });

  it("refreshes when a pending action appears", () => {
    const previous = activity("awaiting_plan_decision", "decision", 8);
    const next = activity(
      "awaiting_plan_decision",
      "decision",
      8,
      pendingAction(),
    );

    expect(shouldRefreshRunPage(previous, next)).toBe(true);
  });

  it.each([
    ["id", { id: "action-2" }],
    ["kind", { kind: "phase" }],
    ["artifact", { artifact_id: "artifact-2" }],
    ["phase", { phase_id: "phase-2" }],
    ["revision", { revision: 2 }],
  ] as const)("refreshes when the pending action %s changes", (_subject, override) => {
    const previous = activity(
      "awaiting_plan_decision",
      "decision",
      8,
      pendingAction(),
    );
    const next = activity(
      "awaiting_plan_decision",
      "decision",
      8,
      pendingAction(override),
    );

    expect(shouldRefreshRunPage(previous, next)).toBe(true);
  });

  it("does not refresh unchanged pending actions", () => {
    const previous = activity(
      "awaiting_plan_decision",
      "decision",
      8,
      pendingAction(),
    );
    const next = activity(
      "awaiting_plan_decision",
      "decision",
      8,
      pendingAction(),
    );

    expect(shouldRefreshRunPage(previous, next)).toBe(false);
  });
});
