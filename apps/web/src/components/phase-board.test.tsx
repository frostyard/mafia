import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PhaseBoard } from "@/components/phase-board";
import { PHASE_STATES, phaseStateTone } from "@/lib/workflow-state";

describe("PhaseBoard", () => {
  it.each(PHASE_STATES)("applies the shared %s tone", (status) => {
    render(
      <PhaseBoard phases={[{
        id: "phase-1", ordinal: 1, title: "Phase", objective: "Objective", dependencies: [], details: {},
        status, plan_revision: 1, source_sha: "source", branch_name: null, commit_sha: null,
        pr_number: null, pr_url: null, merge_sha: null, review_cycle: 0,
        implementation_review_attempts: 0, remediation_attempts: 0, verification_attempts: 0,
        candidate_base_sha: null, candidate_diff_hash: null, project_configuration: null,
      }]} />,
    );

    expect(screen.getByText(status.replaceAll("_", " ")).classList.contains(`tone-${phaseStateTone(status)}`)).toBe(true);
  });
});
