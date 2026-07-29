import type { WorkflowType } from "@/lib/types";

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

function specificationStageIndex(state: string): number {
  if (state === "intake" || state === "failed" || state === "cancelled") return 0;
  if (state.includes("spec")) return 1;
  if (state.includes("plan") || state === "grounding_plan" || state === "reviewing_plan") return 2;
  if (
    state.includes("phase") ||
    state.includes("implementation") ||
    state === "executing_phase" ||
    state === "verifying_remediation" ||
    state === "regrounding"
  ) {
    return 3;
  }
  if (state.includes("pr") || state.includes("merge") || state === "completed") return 4;
  return 0;
}

function pullRequestReviewStageIndex(state: string): number {
  if (state === "grounding_pr_review") return 1;
  if (state === "reviewing_pr") return 2;
  if (state === "consolidating_pr_review") return 3;
  if (
    state === "awaiting_pr_review_decision" ||
    state === "posting_pr_review" ||
    state === "completed"
  ) {
    return 4;
  }
  return 0;
}

export function StageTimeline({
  state,
  workflowType = "specification",
}: {
  state: string;
  workflowType?: WorkflowType;
}) {
  const review = workflowType === "pull_request_review";
  const stages = review ? pullRequestReviewStages : specificationStages;
  const current = review
    ? pullRequestReviewStageIndex(state)
    : specificationStageIndex(state);
  return (
    <ol className="timeline" aria-label="Run progress">
      {stages.map((stage, index) => {
        const status = index < current ? "complete" : index === current ? "current" : "pending";
        return (
          <li className={`timeline-item ${status}`} key={stage.key}>
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
