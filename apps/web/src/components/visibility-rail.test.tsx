import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VisibilityRail } from "@/components/visibility-rail";
import { getRunActivity } from "@/lib/api";
import type { RunActivity } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent: { addMessage: vi.fn(), runAgent: vi.fn() }, isReady: true }),
}));
vi.mock("@/lib/api", () => ({
  cancelRun: vi.fn(),
  getRunActivity: vi.fn(),
  prepareRunRetry: vi.fn(),
}));

function activity(state: string): RunActivity {
  return {
    run_id: "run-1", state, version: 1, status_mode: state === "failed" ? "failed" : "working",
    status_message: "Status", stalled: false, stall_reason: null, stall_threshold_seconds: 0,
    can_cancel: false, can_retry: false, source_sha: null, files_discovered: null,
    citations_found: 0, operations: [], events: [],
  };
}

describe("VisibilityRail polling", () => {
  beforeEach(() => vi.mocked(getRunActivity).mockReset());
  afterEach(() => vi.useRealTimers());

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
});
