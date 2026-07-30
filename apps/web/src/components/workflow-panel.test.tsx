import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowPanel } from "@/components/workflow-panel";
import { resetRunToSpecification } from "@/lib/api";
import { startOrRestoreWorkflow } from "@/lib/workflow-control";

const refresh = vi.fn();
const agent = { addMessage: vi.fn(), runAgent: vi.fn() };
let mockedInterrupt: unknown = null;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent, isReady: false }),
  useAgentContext: vi.fn(),
  useInterrupt: ({ render }: { render: (props: { interrupt: unknown; resolve: () => Promise<unknown> }) => React.ReactNode }) =>
    mockedInterrupt ? render({ interrupt: mockedInterrupt, resolve: vi.fn() }) : null,
}));

vi.mock("@/lib/api", () => ({
  resetRunToSpecification: vi.fn(),
}));

vi.mock("@/lib/workflow-control", () => ({
  startOrRestoreWorkflow: vi.fn(),
}));

describe("WorkflowPanel recovery controls", () => {
  beforeEach(() => {
    mockedInterrupt = null;
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

  it("restores phase approval controls after an interrupted stream", async () => {
    vi.mocked(startOrRestoreWorkflow).mockResolvedValue();
    render(
      <WorkflowPanel
        activeSpecRevision={1}
        runId="run-1"
        runState="ready_for_phase"
        threadId="thread-1"
        workflowType="specification"
      />,
    );

    const restore = screen.getByRole("button", {
      name: "Restore decision controls",
    });
    expect((restore as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(restore);

    await waitFor(() =>
      expect(startOrRestoreWorkflow).toHaveBeenCalledWith(
        agent,
        "run-1",
        true,
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

  it("renders the workflow request prompt from Agent Framework metadata", () => {
    mockedInterrupt = {
      metadata: {
        agent_framework: {
          request_type: "PhaseDecisionRequest",
          data: { prompt: "Start phase 2 using repository validation?" },
        },
      },
    };

    render(
      <WorkflowPanel
        activeSpecRevision={1}
        runId="run-1"
        runState="ready_for_phase"
        threadId="thread-1"
        workflowType="specification"
      />,
    );

    expect(
      screen.getByText("Start phase 2 using repository validation?"),
    ).toBeTruthy();
  });
});
