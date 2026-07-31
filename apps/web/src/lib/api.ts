import type {
  ApiError,
  DecisionPayload,
  Evidence,
  ModelAvailability,
  Project,
  Run,
  RunActivity,
  RunCreate,
  RunDetail,
} from "@/lib/types";

function apiBaseUrl(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  }
  return process.env.MAFIA_API_URL ?? "http://127.0.0.1:8000";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  if (typeof window === "undefined" && process.env.MAFIA_INTERNAL_SECRET) {
    headers.set(
      "X-Mafia-Internal-Secret",
      process.env.MAFIA_INTERNAL_SECRET,
    );
  }
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    let error: ApiError = { message: `Request failed (${response.status}).` };
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const detail = body.detail;
        if (typeof detail === "string") {
          error = { message: detail };
        } else if (typeof detail === "object" && detail !== null) {
          const candidate = detail as Partial<ApiError>;
          error = {
            code: candidate.code,
            message: candidate.message ?? error.message,
          };
        }
      }
    } catch {
      // Preserve the useful HTTP status when the gateway returns non-JSON.
    }
    throw error;
  }
  return (await response.json()) as T;
}

export function getRuns(): Promise<Run[]> {
  return request<Run[]>("/api/runs");
}

export function getProjects(): Promise<Project[]> {
  return request<Project[]>("/api/projects");
}

export function getProject(id: string): Promise<Project> {
  return request<Project>(`/api/projects/${encodeURIComponent(id)}`);
}

export function createProject(repository: string): Promise<Project> {
  return request<Project>("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repository }),
  });
}

export function updateProjectConfiguration(
  id: string,
  content: string,
): Promise<Project> {
  return request<Project>(
    `/api/projects/${encodeURIComponent(id)}/configuration`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
}

export function getRun(id: string): Promise<RunDetail> {
  return request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`);
}

export function getEvidence(id: string): Promise<Evidence[]> {
  return request<Evidence[]>(`/api/runs/${encodeURIComponent(id)}/evidence`);
}

export function getRunActivity(id: string): Promise<RunActivity> {
  return request<RunActivity>(`/api/runs/${encodeURIComponent(id)}/activity`);
}

export function cancelRun(id: string): Promise<RunActivity> {
  return request<RunActivity>(`/api/runs/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
}

export function startRun(id: string): Promise<RunActivity> {
  return request<RunActivity>(`/api/runs/${encodeURIComponent(id)}/start`, {
    method: "POST",
  });
}

export function retryRun(id: string): Promise<RunActivity> {
  return request<RunActivity>(`/api/runs/${encodeURIComponent(id)}/retry`, {
    method: "POST",
  });
}

export function submitDecision(
  runId: string,
  actionId: string,
  payload: DecisionPayload,
): Promise<RunActivity> {
  return request<RunActivity>(
    `/api/runs/${encodeURIComponent(runId)}/decisions/${encodeURIComponent(actionId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function resetRunToSpecification(id: string): Promise<Run> {
  return request<Run>(
    `/api/runs/${encodeURIComponent(id)}/reset-to-specification`,
    { method: "POST" },
  );
}

export function createRun(input: RunCreate): Promise<Run> {
  return request<Run>("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function getModelAvailability(): Promise<ModelAvailability> {
  return request<ModelAvailability>("/api/models");
}

export function refreshRun(id: string): Promise<unknown> {
  return request<unknown>(`/api/runs/${encodeURIComponent(id)}/refresh`, {
    method: "POST",
  });
}
