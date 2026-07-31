import { afterEach, describe, expect, it, vi } from "vitest";
import { retryRun, startRun, submitDecision } from "./api";

describe("workflow REST helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts and retries encoded run IDs without request bodies", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      Response.json({ run_id: "run / one" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await startRun("run / one");
    await retryRun("run / one");

    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/runs/run%20%2F%20one/start",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty("body");
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/runs/run%20%2F%20one/retry",
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "POST" });
    expect(fetchMock.mock.calls[1]?.[1]).not.toHaveProperty("body");
  });

  it("submits a JSON decision to encoded run and action IDs", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      Response.json({ run_id: "run / one" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await submitDecision("run / one", "action / one", {
      action: "refine",
      feedback: "Add a test.",
    });

    const [path, init] = fetchMock.mock.calls[0]!;
    expect(path).toBe(
      "/api/runs/run%20%2F%20one/decisions/action%20%2F%20one",
    );
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ action: "refine", feedback: "Add a test." }),
    });
    expect(new Headers(init?.headers).get("Content-Type")).toBe(
      "application/json",
    );
  });

  it("preserves API errors from decision requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        Response.json(
          { detail: { code: "stale_version", message: "Refresh the run." } },
          { status: 409 },
        ),
      ),
    );

    await expect(
      submitDecision("run-1", "action-1", { action: "accept" }),
    ).rejects.toEqual({ code: "stale_version", message: "Refresh the run." });
  });
});
