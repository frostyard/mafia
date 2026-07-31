import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowPanel } from "@/components/workflow-panel";
import {
  resetRunToSpecification,
  startRun,
  submitDecision,
} from "@/lib/api";
import type { PendingAction, RunDetail } from "@/lib/types";

const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));
vi.mock("@/lib/api", () => ({
  resetRunToSpecification: vi.fn(),
  startRun: vi.fn(),
  submitDecision: vi.fn(),
}));

function action(
  kind: PendingAction["kind"],
  payload: Record<string, unknown> = {},
): PendingAction {
  return {
    id: "action-1",
    kind,
    expected_run_version: 1,
    artifact_id: null,
    phase_id: null,
    revision: null,
    payload,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function run(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: "run-1",
    repository: {
      id: "project-1",
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
    pending_action: null,
    ...overrides,
  };
}

describe("WorkflowPanel", () => {
  beforeEach(() => {
    refresh.mockReset();
    vi.mocked(resetRunToSpecification).mockReset();
    vi.mocked(startRun).mockReset();
    vi.mocked(submitDecision).mockReset();
  });

  it("starts an intake run and refreshes after success", async () => {
    vi.mocked(startRun).mockResolvedValue({} as never);
    render(<WorkflowPanel run={run()} />);

    fireEvent.click(screen.getByRole("button", { name: "Start workflow" }));

    await waitFor(() => expect(startRun).toHaveBeenCalledWith("run-1"));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("uses review-specific start copy", () => {
    render(<WorkflowPanel run={run({ workflow_type: "pull_request_review" })} />);

    expect(screen.getByRole("button", { name: "Start review" })).toBeTruthy();
  });

  it("submits artifact acceptance and refinement with exact payloads", async () => {
    vi.mocked(submitDecision).mockResolvedValue({} as never);
    const view = render(
      <WorkflowPanel run={run({ pending_action: action("specification") })} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "accept",
      }),
    );
    view.unmount();

    render(<WorkflowPanel run={run({ pending_action: action("plan") })} />);
    const refine = screen.getByRole("button", { name: "Refine" });
    expect((refine as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Refinement feedback"), {
      target: { value: "Clarify acceptance criteria" },
    });
    fireEvent.click(refine);
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "refine",
        feedback: "Clarify acceptance criteria",
      }),
    );
  });

  it("submits phase, review, cancellation, and configuration actions", async () => {
    vi.mocked(submitDecision).mockResolvedValue({} as never);
    const view = render(
      <WorkflowPanel run={run({ pending_action: action("phase") })} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Start phase" }));
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "start",
      }),
    );
    view.unmount();

    render(
      <WorkflowPanel
        run={run({ pending_action: action("pull_request_review") })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Post to pull request" }));
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "post",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Finish without posting" }));
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "finish",
      }),
    );
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" }).at(-1)!);
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "cancel",
      }),
    );
    view.unmount();

    render(
      <WorkflowPanel
        run={run({
          pending_action: action("configuration_required", {
            project_id: "project-2",
          }),
        })}
      />,
    );
    expect(screen.getByRole("link", { name: "Open project settings" }).getAttribute("href")).toBe("/projects/project-2");
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "check_again",
      }),
    );
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" }).at(-1)!);
    await waitFor(() =>
      expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", {
        action: "cancel",
      }),
    );
  });

  it("shows backend errors, disables submissions, and confirms reset", async () => {
    let resolveDecision!: () => void;
    vi.mocked(submitDecision).mockImplementation(
      () => new Promise((resolve) => { resolveDecision = () => resolve({} as never); }),
    );
    vi.mocked(resetRunToSpecification).mockResolvedValue({} as never);
    const view = render(<WorkflowPanel run={run({ pending_action: action("phase") })} />);

    fireEvent.click(screen.getByRole("button", { name: "Start phase" }));
    expect((screen.getByRole("button", { name: "Starting..." }) as HTMLButtonElement).disabled).toBe(true);
    resolveDecision();
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    view.unmount();

    render(<WorkflowPanel run={run({ active_spec_revision: 1, state: "failed" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Adjust specification" }));
    expect(resetRunToSpecification).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm adjustment" }));
    await waitFor(() => expect(resetRunToSpecification).toHaveBeenCalledWith("run-1"));

    vi.mocked(submitDecision).mockRejectedValue({ message: "Decision rejected" });
    render(<WorkflowPanel run={run({ pending_action: action("phase") })} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Start phase" })[0]);
    expect((await screen.findByRole("alert")).textContent).toContain("Decision rejected");
  });

  it("uses accepting copy while artifact acceptance is pending", () => {
    vi.mocked(submitDecision).mockImplementation(() => new Promise(() => {}));
    render(<WorkflowPanel run={run({ pending_action: action("specification") })} />);

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    expect(screen.getByRole("button", { name: "Accepting..." })).toBeTruthy();
  });

  it("URL-encodes the configuration project ID", () => {
    render(
      <WorkflowPanel
        run={run({
          pending_action: action("configuration_required", { project_id: "project /?" }),
        })}
      />,
    );

    expect(screen.getByRole("link", { name: "Open project settings" }).getAttribute("href")).toBe("/projects/project%20%2F%3F");
  });

  it("renders no inferred restore, durable-thread, or connection controls", () => {
    render(<WorkflowPanel run={run({ state: "ready_for_phase" })} />);

    expect(screen.queryByText("Restore decision controls")).toBeNull();
    expect(screen.queryByText(/Durable thread/)).toBeNull();
    expect(screen.queryByText(/connection is not ready/i)).toBeNull();
  });
});
