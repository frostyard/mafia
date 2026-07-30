export const RUN_STATES = [
  "intake", "generating_spec", "awaiting_spec_decision", "grounding_plan",
  "generating_plan", "reviewing_plan", "adjudicating_plan", "persisting_plan",
  "awaiting_plan_decision", "ready_for_phase", "executing_phase",
  "reviewing_implementation", "adjudicating_implementation",
  "remediating_implementation", "verifying_remediation", "pr_open",
  "waiting_for_merge", "regrounding", "grounding_pr_review", "reviewing_pr",
  "consolidating_pr_review", "awaiting_pr_review_decision", "posting_pr_review",
  "completed", "failed", "cancelled",
] as const;

export const PHASE_STATES = [
  "pending", "ready", "executing", "waiting_for_merge", "merged", "failed",
] as const;

export const OPERATION_STATUSES = [
  "running", "completed", "failed", "timed_out", "cancelled",
] as const;

export type RunState = (typeof RUN_STATES)[number];
export type PhaseState = (typeof PHASE_STATES)[number];
export type OperationStatus = (typeof OPERATION_STATUSES)[number];
export type StateTone = "idle" | "working" | "decision" | "success" | "danger" | "external";

const decisionStates = new Set<RunState>([
  "awaiting_spec_decision", "awaiting_plan_decision", "ready_for_phase",
  "awaiting_pr_review_decision",
]);
const terminalStates = new Set<RunState>(["completed", "failed", "cancelled"]);

function assertNever(value: never): never {
  throw new Error(`Unhandled workflow state: ${value}`);
}

export const isDecisionState = (state: RunState) => decisionStates.has(state);
export const isTerminalState = (state: RunState) => terminalStates.has(state);

export function runStateTone(state: RunState): StateTone {
  switch (state) {
    case "intake":
      return "idle";
    case "awaiting_spec_decision":
    case "awaiting_plan_decision":
    case "ready_for_phase":
    case "awaiting_pr_review_decision":
      return "decision";
    case "completed":
      return "success";
    case "failed":
    case "cancelled":
      return "danger";
    case "pr_open":
    case "waiting_for_merge":
      return "external";
    case "generating_spec":
    case "grounding_plan":
    case "generating_plan":
    case "reviewing_plan":
    case "adjudicating_plan":
    case "persisting_plan":
    case "executing_phase":
    case "reviewing_implementation":
    case "adjudicating_implementation":
    case "remediating_implementation":
    case "verifying_remediation":
    case "regrounding":
    case "grounding_pr_review":
    case "reviewing_pr":
    case "consolidating_pr_review":
    case "posting_pr_review":
      return "working";
    default:
      return assertNever(state);
  }
}

export function phaseStateTone(state: PhaseState): StateTone {
  switch (state) {
    case "pending":
      return "idle";
    case "ready":
      return "decision";
    case "executing":
      return "working";
    case "waiting_for_merge":
      return "external";
    case "merged":
      return "success";
    case "failed":
      return "danger";
    default:
      return assertNever(state);
  }
}
