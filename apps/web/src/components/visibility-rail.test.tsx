import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VisibilityRail } from "@/components/visibility-rail";
import { getRunActivity, retryRun } from "@/lib/api";
import type { RunActivity } from "@/lib/types";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("@/lib/api", () => ({
  cancelRun: vi.fn(),
  getRunActivity: vi.fn(),
  retryRun: vi.fn(),
}));

function activity(state: string, canRetry = false): RunActivity {
  return {
    run_id: "run-1", state, version: 1, status_mode: state === "failed" ? "failed" : "working",
    status_message: "Status", stalled: false, stall_reason: null, stall_threshold_seconds: 0,
    can_cancel: false, can_retry: canRetry, source_sha: null, files_discovered: null,
    citations_found: 0, pending_action: null, operations: [], events: [],
  };
}

describe("VisibilityRail polling and controls", () => {
  beforeEach(() => {
    refresh.mockReset();
    vi.mocked(getRunActivity).mockReset();
    vi.mocked(retryRun).mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("retries only when the activity permits it and refreshes", async () => {
    vi.mocked(retryRun).mockResolvedValue(activity("failed", true));
    render(<VisibilityRail initialActivity={activity("failed", true)} runId="run-1" workflowType="specification" />);

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retryRun).toHaveBeenCalledWith("run-1"));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("does not offer retry when the activity does not permit it", () => {
    render(<VisibilityRail initialActivity={activity("failed")} runId="run-1" workflowType="specification" />);

    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("does not schedule a poll for initially terminal activity", () => {
    vi.useFakeTimers();
    const view = render(<VisibilityRail initialActivity={activity("failed")} runId="run-1" workflowType="specification" />);
    act(() => vi.advanceTimersByTime(3_000));
    expect(getRunActivity).not.toHaveBeenCalled();
    view.unmount();
  });

  it("stops polling after a response becomes terminal", async () => {
    vi.useFakeTimers();
    vi.mocked(getRunActivity).mockResolvedValue(activity("completed"));
    const view = render(<VisibilityRail initialActivity={activity("generating_plan")} runId="run-1" workflowType="specification" />);
    await act(async () => vi.advanceTimersByTimeAsync(3_000));
    await act(async () => vi.advanceTimersByTimeAsync(3_000));
    expect(getRunActivity).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it("renders activity event times in UTC with seconds", () => {
    const originalTimeZone = process.env.TZ;
    try {
      process.env.TZ = "America/Los_Angeles";
      render(
        <VisibilityRail
          initialActivity={{
            ...activity("failed"),
            events: [{
              id: "event-1", event_type: "transition", from_state: "intake", to_state: "failed",
              payload: {}, actor: "system", created_at: "2026-07-30T12:00:00Z",
            }],
          }}
          runId="run-1"
          workflowType="specification"
        />,
      );

      expect(screen.getByText("12:00:00 PM")).toBeTruthy();
    } finally {
      if (originalTimeZone === undefined) delete process.env.TZ;
      else process.env.TZ = originalTimeZone;
    }
  });
});
