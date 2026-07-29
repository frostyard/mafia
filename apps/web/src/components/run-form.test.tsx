import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunForm } from "@/components/run-form";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

describe("RunForm", () => {
  const modelAvailability = {
    pairs: [
      {
        primary_model: "claude-opus-4.8",
        reviewer_model: "gpt-5.6-sol",
      },
      {
        primary_model: "gpt-5.6-sol",
        reviewer_model: "claude-opus-4.8",
      },
    ],
    required: ["claude-opus-4.8", "gpt-5.6-sol"],
    available: ["claude-opus-4.8", "gpt-5.6-sol"],
    missing: [],
  };

  beforeEach(() => {
    push.mockReset();
  });

  it("switches between issue and written requirement inputs", () => {
    render(<RunForm modelAvailability={modelAvailability} />);

    expect(screen.getByLabelText("Issue number or URL")).toBeTruthy();
    expect(screen.getByText("Independent review will use GPT-5.6 Sol.")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Written requirement"));

    expect(screen.getByLabelText("Requirement")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Review pull request"));

    expect(screen.getByLabelText("Pull request number or URL")).toBeTruthy();
    expect(screen.getByLabelText("Adjudicator model")).toBeTruthy();
    expect(
      screen.getByText(
        "Claude Opus 4.8 and GPT-5.6 Sol review independently. Claude Opus 4.8 consolidates their findings.",
      ),
    ).toBeTruthy();
  });

  it("disables pairs when either configured model is missing", () => {
    render(
      <RunForm
        modelAvailability={{
          pairs: modelAvailability.pairs,
          required: ["claude-opus-4.8", "gpt-5.6-sol"],
          available: ["gpt-5.6-sol"],
          missing: ["claude-opus-4.8"],
        }}
      />,
    );

    expect((screen.getByRole("option", { name: "Claude Opus 4.8 (unavailable)" }) as HTMLOptionElement).disabled).toBe(true);
    expect((screen.getByRole("option", { name: "GPT-5.6 Sol (unavailable)" }) as HTMLOptionElement).disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Create run" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
