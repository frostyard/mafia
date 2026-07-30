import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RunPage from "./page";
import { getEvidence, getRun, getRunActivity } from "@/lib/api";
import type { RunActivity, RunDetail } from "@/lib/types";

vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/navigation", () => ({ notFound: vi.fn() }));
vi.mock("@/components/artifact-tabs", () => ({ ArtifactTabs: () => null }));
vi.mock("@/components/phase-board", () => ({ PhaseBoard: () => null }));
vi.mock("@/components/refresh-pr-status", () => ({ RefreshPrStatus: () => null }));
vi.mock("@/components/run-agent-shell", () => ({ RunAgentShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("@/components/run-cards", () => ({ StateBadge: () => null }));
vi.mock("@/components/stage-timeline", () => ({ StageTimeline: () => null }));
vi.mock("@/components/workflow-panel", () => ({ WorkflowPanel: () => null }));
vi.mock("@/lib/api", () => ({
  getEvidence: vi.fn(),
  getRun: vi.fn(),
  getRunActivity: vi.fn(),
}));

const run = {
  id: "run-1",
  repository: {
    id: "repository-1",
    owner: "frostyard",
    name: "mafia",
    remote_url: "https://github.com/frostyard/mafia",
    default_branch: "main",
    last_fetched_sha: null,
  },
  workflow_type: "specification",
  requirement_type: "text",
  issue_number: null,
  requirement_text: "Requirement",
  pull_request_number: null,
  primary_model: "primary",
  reviewer_model: "reviewer",
  thread_id: "thread-1",
  state: "intake",
  version: 1,
  active_spec_revision: null,
  active_plan_revision: null,
  active_review_revision: null,
  project_configuration: null,
  failure_code: null,
  failure_message: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  artifacts: [],
  phases: [],
} as RunDetail;

const activity = {
  run_id: "run-1",
  state: "intake",
  version: 1,
  status_mode: "idle",
  status_message: "Waiting",
  stalled: false,
  stall_reason: null,
  stall_threshold_seconds: 0,
  can_cancel: false,
  can_retry: false,
  source_sha: null,
  files_discovered: null,
  citations_found: 0,
  operations: [],
  events: [],
} as RunActivity;

describe("RunPage", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockResolvedValue(run);
    vi.mocked(getRunActivity).mockResolvedValue(activity);
    vi.mocked(getEvidence).mockRejectedValue(new Error("Evidence unavailable"));
  });

  it("renders run details when evidence loading fails", async () => {
    render(await RunPage({ params: Promise.resolve({ id: "run-1" }) }));

    expect(screen.getByRole("heading", { name: "frostyard/mafia" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain(
      "Source evidence is unavailable.",
    );
    expect(screen.queryByText("We could not load this run.")).toBeNull();
  });
});
