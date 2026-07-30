import type { RunActivity, WorkflowType } from "@/lib/types";
import {
  isRunState,
  isTerminalState,
  runStateTone,
  type RunState,
} from "@/lib/workflow-state";

const specificationStages = [
  { key: "intake", label: "Intake" },
  { key: "spec", label: "Specification" },
  { key: "plan", label: "Plan and review" },
  { key: "phase", label: "Implementation" },
  { key: "pr", label: "Pull request" },
] as const;

const pullRequestReviewStages = [
  { key: "intake", label: "Intake" },
  { key: "ground", label: "Ground pull request" },
  { key: "review", label: "Independent reviews" },
  { key: "consolidate", label: "Adjudication" },
  { key: "publish", label: "Publish decision" },
] as const;

const specificationStageByState: Record<RunState, number> = {
  intake: 0, generating_spec: 1, awaiting_spec_decision: 1,
  grounding_plan: 2, generating_plan: 2, reviewing_plan: 2, adjudicating_plan: 2,
  persisting_plan: 2, awaiting_plan_decision: 2, ready_for_phase: 3,
  executing_phase: 3, reviewing_implementation: 3, adjudicating_implementation: 3,
  remediating_implementation: 3, verifying_remediation: 3, regrounding: 3,
  pr_open: 4, waiting_for_merge: 4, grounding_pr_review: 4, reviewing_pr: 4,
  consolidating_pr_review: 4, awaiting_pr_review_decision: 4, posting_pr_review: 4,
  completed: 4, failed: 0, cancelled: 0,
};

const pullRequestReviewStageByState: Record<RunState, number> = {
  intake: 0, grounding_pr_review: 1, reviewing_pr: 2, consolidating_pr_review: 3,
  awaiting_pr_review_decision: 4, posting_pr_review: 4, completed: 4,
  generating_spec: 0, awaiting_spec_decision: 0, grounding_plan: 0, generating_plan: 0,
  reviewing_plan: 0, adjudicating_plan: 0, persisting_plan: 0, awaiting_plan_decision: 0,
  ready_for_phase: 0, executing_phase: 0, reviewing_implementation: 0,
  adjudicating_implementation: 0, remediating_implementation: 0, verifying_remediation: 0,
  pr_open: 0, waiting_for_merge: 0, regrounding: 0, failed: 0, cancelled: 0,
};

export function stageForState(state: RunState, workflowType: WorkflowType): number {
  return (workflowType === "pull_request_review"
    ? pullRequestReviewStageByState
    : specificationStageByState)[state];
}

export function lastMeaningfulState(activity: Pick<RunActivity, "events">): RunState | undefined {
  for (const event of activity.events) {
    for (const state of [event.to_state, event.from_state]) {
      if (isRunState(state) && !isTerminalState(state)) return state;
    }
  }
}

export function StageTimeline({
  state,
  workflowType = "specification",
  activity,
}: {
  state: string;
  workflowType?: WorkflowType;
  activity?: RunActivity["events"];
}) {
  const review = workflowType === "pull_request_review";
  const stages = review ? pullRequestReviewStages : specificationStages;
  const terminal = isRunState(state) && isTerminalState(state);
  const stageState = terminal && activity
    ? lastMeaningfulState({ events: activity }) ?? state
    : state;
  const current = isRunState(stageState) ? stageForState(stageState, workflowType) : 0;
  const tone = isRunState(state) ? runStateTone(state) : "idle";
  return (
    <ol className="timeline" aria-label="Run progress">
      {stages.map((stage, index) => {
        const status = index < current ? "complete" : index === current ? "current" : "pending";
        return (
          <li
            className={`timeline-item ${status}${index === current ? ` tone-${tone}` : ""}`}
            key={stage.key}
          >
            <span className="timeline-dot" aria-hidden="true">
              {status === "complete" ? "ok" : index + 1}
            </span>
            <span>{stage.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
