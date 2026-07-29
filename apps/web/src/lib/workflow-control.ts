import type { AbstractAgent } from "@ag-ui/client";

type WorkflowAgent = Pick<AbstractAgent, "addMessage" | "runAgent">;

export async function startOrRestoreWorkflow(
  agent: WorkflowAgent,
  runId: string,
  restoreDecision: boolean,
): Promise<void> {
  if (!restoreDecision) {
    agent.addMessage({
      id: crypto.randomUUID(),
      role: "user",
      content: `Start workflow run ${runId}.`,
    });
  }
  await agent.runAgent();
}
