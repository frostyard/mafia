import type { AbstractAgent } from "@ag-ui/client";
import { describe, expect, it, vi } from "vitest";
import { startOrRestoreWorkflow } from "@/lib/workflow-control";

type WorkflowAgent = Pick<AbstractAgent, "addMessage" | "runAgent">;

function testAgent(): {
  agent: WorkflowAgent;
  addMessage: ReturnType<typeof vi.fn>;
  runAgent: ReturnType<typeof vi.fn>;
} {
  const addMessage = vi.fn();
  const runAgent = vi.fn().mockResolvedValue({
    result: undefined,
    newMessages: [],
  });
  return {
    agent: { addMessage, runAgent } as WorkflowAgent,
    addMessage,
    runAgent,
  };
}

describe("startOrRestoreWorkflow", () => {
  it("reconnects without adding a message when restoring a decision", async () => {
    const { agent, addMessage, runAgent } = testAgent();

    await startOrRestoreWorkflow(agent, "run-1", true);

    expect(addMessage).not.toHaveBeenCalled();
    expect(runAgent).toHaveBeenCalledOnce();
  });

  it("adds a start message for a new workflow", async () => {
    const { agent, addMessage, runAgent } = testAgent();

    await startOrRestoreWorkflow(agent, "run-1", false);

    expect(addMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        role: "user",
        content: "Start workflow run run-1.",
      }),
    );
    expect(runAgent).toHaveBeenCalledOnce();
  });
});
