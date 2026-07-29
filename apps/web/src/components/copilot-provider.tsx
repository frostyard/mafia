"use client";

import { CopilotKit } from "@copilotkit/react-core/v2";
import type { ReactNode } from "react";

export function CopilotProvider({ children }: { children: ReactNode }) {
  return (
    <CopilotKit
      runtimeUrl={process.env.NEXT_PUBLIC_COPILOTKIT_RUNTIME_URL ?? "/api/copilotkit"}
    >
      {children}
    </CopilotKit>
  );
}
