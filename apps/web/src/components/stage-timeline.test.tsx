import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StageTimeline } from "@/components/stage-timeline";
import type { RunState } from "@/lib/workflow-state";

const specificationStageLabels: Record<RunState, string> = {
  intake: "Intake", generating_spec: "Specification", awaiting_spec_decision: "Specification",
  grounding_plan: "Plan and review", generating_plan: "Plan and review", reviewing_plan: "Plan and review",
  adjudicating_plan: "Plan and review", persisting_plan: "Plan and review", awaiting_plan_decision: "Plan and review",
  ready_for_phase: "Implementation", executing_phase: "Implementation", reviewing_implementation: "Implementation",
  adjudicating_implementation: "Implementation", remediating_implementation: "Implementation", verifying_remediation: "Implementation",
  regrounding: "Implementation", pr_open: "Pull request", waiting_for_merge: "Pull request",
  grounding_pr_review: "Pull request", reviewing_pr: "Pull request", consolidating_pr_review: "Pull request",
  awaiting_pr_review_decision: "Pull request", posting_pr_review: "Pull request", completed: "Pull request",
  failed: "Intake", cancelled: "Intake",
};

const pullRequestReviewStageLabels: Record<RunState, string> = {
  intake: "Intake", grounding_pr_review: "Ground pull request", reviewing_pr: "Independent reviews",
  consolidating_pr_review: "Adjudication", awaiting_pr_review_decision: "Publish decision",
  posting_pr_review: "Publish decision", completed: "Publish decision", generating_spec: "Intake",
  awaiting_spec_decision: "Intake", grounding_plan: "Intake", generating_plan: "Intake",
  reviewing_plan: "Intake", adjudicating_plan: "Intake", persisting_plan: "Intake",
  awaiting_plan_decision: "Intake", ready_for_phase: "Intake", executing_phase: "Intake",
  reviewing_implementation: "Intake", adjudicating_implementation: "Intake", remediating_implementation: "Intake",
  verifying_remediation: "Intake", pr_open: "Intake", waiting_for_merge: "Intake", regrounding: "Intake",
  failed: "Intake", cancelled: "Intake",
};

describe("StageTimeline", () => {
  it.each([
    "grounding_plan",
    "generating_plan",
    "reviewing_plan",
    "adjudicating_plan",
    "persisting_plan",
  ])("marks %s as plan work", (state) => {
    render(<StageTimeline state={state} />);

    expect(screen.getByLabelText("Run progress").textContent).toContain("Plan and review");
    expect(document.querySelector(".timeline-item.current")?.textContent).toContain("Plan and review");
  });

  it.each([
    "reviewing_implementation",
    "adjudicating_implementation",
    "remediating_implementation",
    "verifying_remediation",
  ])("marks %s as implementation work", (state) => {
    render(<StageTimeline state={state} />);

    expect(document.querySelector(".timeline-item.current")?.textContent).toContain(
      "Implementation",
    );
  });

  it("renders pull request review stages", () => {
    render(
      <StageTimeline
        state="reviewing_pr"
        workflowType="pull_request_review"
      />,
    );

    expect(document.querySelector(".timeline-item.current")?.textContent).toContain(
      "Independent reviews",
    );
  });

  it.each(Object.entries(specificationStageLabels))("maps specification state %s to %s", (state, label) => {
    render(<StageTimeline state={state} />);

    expect(document.querySelector(".timeline-item.current")?.textContent).toContain(label);
  });

  it.each(Object.entries(pullRequestReviewStageLabels))("maps pull request review state %s to %s", (state, label) => {
    render(<StageTimeline state={state} workflowType="pull_request_review" />);

    expect(document.querySelector(".timeline-item.current")?.textContent).toContain(label);
  });

  it.each(["failed", "cancelled"])("preserves the last active stage for %s", (state) => {
    render(
      <StageTimeline
        activity={[
          {
            id: "terminal",
            event_type: "transition",
            from_state: "generating_plan",
            to_state: state,
            payload: {},
            actor: "system",
            created_at: "2026-07-30T12:01:00Z",
          },
          {
            id: "entered-plan",
            event_type: "transition",
            from_state: "generating_spec",
            to_state: "generating_plan",
            payload: {},
            actor: "system",
            created_at: "2026-07-30T12:00:00Z",
          },
        ]}
        state={state}
      />,
    );

    expect(document.querySelector(".timeline-item.current")?.textContent).toContain("Plan and review");
    expect(document.querySelector(".timeline-item.current")?.classList.contains("tone-danger")).toBe(true);
  });

  it("ignores nullable and unknown event states while preserving a terminal stage", () => {
    render(
      <StageTimeline
        activity={[
          {
            id: "invalid",
            event_type: "transition",
            from_state: null,
            to_state: "unknown_state",
            payload: {},
            actor: "system",
            created_at: "2026-07-30T12:01:00Z",
          },
          {
            id: "known",
            event_type: "transition",
            from_state: "generating_spec",
            to_state: "generating_plan",
            payload: {},
            actor: "system",
            created_at: "2026-07-30T12:00:00Z",
          },
        ]}
        state="failed"
      />,
    );

    expect(document.querySelector(".timeline-item.current")?.textContent).toContain("Plan and review");
  });
});
