import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RunPage from "./page";
import { notFound } from "next/navigation";
import { getEvidence, getRun, getRunActivity } from "@/lib/api";
import type { RunActivity, RunDetail } from "@/lib/types";

vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/navigation", () => ({ notFound: vi.fn() }));
vi.mock("@/components/artifact-tabs", () => ({ ArtifactTabs: () => null }));
vi.mock("@/components/phase-board", () => ({ PhaseBoard: () => null }));
vi.mock("@/components/refresh-pr-status", () => ({ RefreshPrStatus: () => <span>Refresh PR status</span> }));
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
    vi.mocked(notFound).mockReset();
    vi.mocked(getEvidence).mockReset();
    vi.mocked(getRun).mockReset();
    vi.mocked(getRunActivity).mockReset();
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

  it("invokes notFound when the run does not exist", async () => {
    vi.mocked(getRun).mockRejectedValue({
      code: "run_not_found",
      message: "Run not found",
    });
    vi.mocked(getEvidence).mockResolvedValue([]);

    await RunPage({ params: Promise.resolve({ id: "missing-run" }) });

    expect(notFound).toHaveBeenCalledOnce();
  });

  it("renders the unavailable state when essential run loading fails", async () => {
    vi.mocked(getRun).mockRejectedValue(new Error("API unavailable"));
    vi.mocked(getEvidence).mockResolvedValue([]);

    render(await RunPage({ params: Promise.resolve({ id: "run-1" }) }));

    expect(
      screen.getByRole("heading", { name: "We could not load this run." }),
    ).toBeTruthy();
    expect(notFound).not.toHaveBeenCalled();
  });

  it("only renders PR refresh while a specification run waits for merge", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...run, state: "waiting_for_merge" });
    const waitingForMerge = render(await RunPage({ params: Promise.resolve({ id: "run-1" }) }));
    expect(screen.getByText("Refresh PR status")).toBeTruthy();
    waitingForMerge.unmount();

    vi.mocked(getRun).mockResolvedValue({ ...run, state: "pr_open" });
    render(await RunPage({ params: Promise.resolve({ id: "run-1" }) }));
    expect(screen.queryByText("Refresh PR status")).toBeNull();
  });
});
