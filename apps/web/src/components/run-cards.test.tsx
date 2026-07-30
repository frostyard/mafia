import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StateBadge } from "@/components/run-cards";
import { RUN_STATES, runStateTone } from "@/lib/workflow-state";

describe("StateBadge", () => {
  it.each(RUN_STATES)("applies the shared %s tone", (state) => {
    render(<StateBadge state={state} />);

    expect(screen.getByText(state.replaceAll("_", " ")).classList.contains(`tone-${runStateTone(state)}`)).toBe(true);
  });
});
