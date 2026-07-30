import { describe, expect, it } from "vitest";
import {
  RUN_STATES,
  isDecisionState,
  isTerminalState,
  runStateTone,
} from "@/lib/workflow-state";
import type { RunState } from "@/lib/workflow-state";

describe("workflow state contract", () => {
  it("classifies every operator decision state", () => {
    expect(RUN_STATES.filter(isDecisionState)).toEqual([
      "awaiting_spec_decision",
      "awaiting_plan_decision",
      "ready_for_phase",
      "awaiting_pr_review_decision",
    ]);
  });

  it("classifies terminal states and gives every state a tone", () => {
    expect((["completed", "failed", "cancelled"] as RunState[]).every(isTerminalState)).toBe(true);
    expect(RUN_STATES.every((state) => runStateTone(state) !== undefined)).toBe(true);
  });
});
