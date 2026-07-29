import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StageTimeline } from "@/components/stage-timeline";

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
});
