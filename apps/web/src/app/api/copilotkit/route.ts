import { HttpAgent } from "@ag-ui/client";
import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";

process.env.COPILOTKIT_TELEMETRY_DISABLED ??= "true";

const runtime = new CopilotRuntime({
  agents: {
    mafia: new HttpAgent({
      url: process.env.AGENT_URL ?? "http://127.0.0.1:8000/ag-ui",
      headers: process.env.MAFIA_INTERNAL_SECRET
        ? {
            "X-Mafia-Internal-Secret": process.env.MAFIA_INTERNAL_SECRET,
          }
        : undefined,
    }),
  },
});

const handler = createCopilotRuntimeHandler({
  runtime,
  basePath: "/api/copilotkit",
  mode: "single-route",
});

export const POST = handler;
