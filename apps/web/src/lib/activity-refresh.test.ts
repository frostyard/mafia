import { describe, expect, it } from "vitest";
import { shouldRefreshRunPage } from "./activity-refresh";
import type { RunActivity } from "@/lib/types";

function activity(
  state: RunActivity["state"],
  statusMode: RunActivity["status_mode"],
  version: number,
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
    operations: [],
    events: [],
  };
}

describe("shouldRefreshRunPage", () => {
  it("does not refresh while a streamed workflow remains active", () => {
    const previous = activity("generating_plan", "working", 4);
    const next = activity("reviewing_plan", "working", 5);

    expect(shouldRefreshRunPage(previous, next)).toBe(false);
  });

  it("refreshes when work reaches a durable decision", () => {
    const previous = activity("persisting_plan", "working", 7);
    const next = activity("awaiting_plan_decision", "decision", 8);

    expect(shouldRefreshRunPage(previous, next)).toBe(true);
  });

  it("does not refresh unchanged projections", () => {
    const previous = activity("awaiting_plan_decision", "decision", 8);

    expect(shouldRefreshRunPage(previous, previous)).toBe(false);
  });
});
