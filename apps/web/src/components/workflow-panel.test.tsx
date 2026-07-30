import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowPanel } from "@/components/workflow-panel";
import { resetRunToSpecification } from "@/lib/api";
import { startOrRestoreWorkflow } from "@/lib/workflow-control";

const refresh = vi.fn();
const agent = { addMessage: vi.fn(), runAgent: vi.fn() };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent, isReady: false }),
  useAgentContext: vi.fn(),
  useInterrupt: () => null,
}));

vi.mock("@/lib/api", () => ({
  resetRunToSpecification: vi.fn(),
}));

vi.mock("@/lib/workflow-control", () => ({
  startOrRestoreWorkflow: vi.fn(),
}));

describe("WorkflowPanel recovery controls", () => {
  beforeEach(() => {
    refresh.mockReset();
    vi.mocked(resetRunToSpecification).mockReset();
    vi.mocked(startOrRestoreWorkflow).mockReset();
  });

  it("allows a failed workflow to attempt reconnection", async () => {
    vi.mocked(startOrRestoreWorkflow).mockResolvedValue();
    render(
      <WorkflowPanel
        activeSpecRevision={1}
        runId="run-1"
        runState="failed"
        threadId="thread-1"
        workflowType="specification"
      />,
    );

    const retry = screen.getByRole("button", { name: "Retry workflow" });
    expect((retry as HTMLButtonElement).disabled).toBe(false);
    expect(
      screen.getByText(
        "The agent connection is not ready. This action will attempt to reconnect it.",
      ),
    ).toBeTruthy();

    fireEvent.click(retry);

    await waitFor(() =>
      expect(startOrRestoreWorkflow).toHaveBeenCalledWith(
        agent,
        "run-1",
        false,
      ),
    );
  });

  it("confirms specification reset in the page before sending it", async () => {
    vi.mocked(resetRunToSpecification).mockResolvedValue({} as never);
    render(
      <WorkflowPanel
        activeSpecRevision={1}
        runId="run-1"
        runState="failed"
        threadId="thread-1"
        workflowType="specification"
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Adjust specification" }),
    );

    expect(resetRunToSpecification).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Unstarted phases and the active plan will be discarded/),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "Confirm adjustment" }),
    );

    await waitFor(() =>
      expect(resetRunToSpecification).toHaveBeenCalledWith("run-1"),
    );
    expect(refresh).toHaveBeenCalledOnce();
  });
});
