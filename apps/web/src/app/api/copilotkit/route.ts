import { HttpAgent } from "@ag-ui/client";
import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";

process.env.COPILOTKIT_TELEMETRY_DISABLED ??= "true";

export async function POST(request: Request): Promise<Response> {
  const headers: Record<string, string> = {};
  if (process.env.MAFIA_INTERNAL_SECRET) {
    headers["X-Mafia-Internal-Secret"] = process.env.MAFIA_INTERNAL_SECRET;
  }
  const operatorId = request.headers.get("x-mafia-github-user-id");
  const operatorLogin = request.headers.get("x-mafia-github-login");
  if (
    process.env.MAFIA_AUTH_MODE?.toLowerCase() === "github" &&
    operatorId &&
    operatorLogin
  ) {
    headers["X-Mafia-Operator-ID"] = operatorId;
    headers["X-Mafia-Operator-Login"] = operatorLogin;
  }
  const runtime = new CopilotRuntime({
    agents: {
      mafia: new HttpAgent({
        url: process.env.AGENT_URL ?? "http://127.0.0.1:8000/ag-ui",
        headers,
      }),
    },
  });
  return createCopilotRuntimeHandler({
    runtime,
    basePath: "/api/copilotkit",
    mode: "single-route",
  })(request);
}
