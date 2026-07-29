"use client";

import { CopilotChatConfigurationProvider } from "@copilotkit/react-core/v2";
import type { ReactNode } from "react";
import { VisibilityRail } from "@/components/visibility-rail";
import type { RunActivity, WorkflowType } from "@/lib/types";

export function RunAgentShell({
  children,
  initialActivity,
  runId,
  threadId,
  workflowType,
}: {
  children: ReactNode;
  initialActivity: RunActivity;
  runId: string;
  threadId: string;
  workflowType: WorkflowType;
}) {
  return (
    <CopilotChatConfigurationProvider agentId="mafia" threadId={threadId}>
      <div className="run-workspace-layout">
        <main className="run-workspace-main">{children}</main>
        <VisibilityRail
          initialActivity={initialActivity}
          runId={runId}
          workflowType={workflowType}
        />
      </div>
    </CopilotChatConfigurationProvider>
  );
}
