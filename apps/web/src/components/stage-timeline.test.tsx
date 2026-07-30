import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StageTimeline } from "@/components/stage-timeline";
import { RUN_STATES } from "@/lib/workflow-state";

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

  it.each(RUN_STATES)("maps specification state %s to a stage", (state) => {
    render(<StageTimeline state={state} />);

    expect(document.querySelector(".timeline-item.current")).toBeTruthy();
  });

  it.each(RUN_STATES)("maps pull request review state %s to a stage", (state) => {
    render(<StageTimeline state={state} workflowType="pull_request_review" />);

    expect(document.querySelector(".timeline-item.current")).toBeTruthy();
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
