import { afterEach, describe, expect, it, vi } from "vitest";
import { proxyApiRequest } from "@/lib/api-proxy";

describe("proxyApiRequest", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.MAFIA_API_URL;
  });

  it("uses the runtime API URL and preserves request details", async () => {
    process.env.MAFIA_API_URL = "http://127.0.0.1:18087";
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response('{"ok":true}', {
          headers: { "content-type": "application/json" },
          status: 202,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request(
      "http://127.0.0.1:13087/api/runs/run-1/retry?source=ui",
      {
        body: JSON.stringify({ retry: true }),
        headers: { "content-type": "application/json", host: "localhost" },
        method: "POST",
      },
    );

    const response = await proxyApiRequest(
      request,
      "/api/runs/run-1/retry",
    );
    const [target, init] = fetchMock.mock.calls[0]!;

    expect(String(target)).toBe(
      "http://127.0.0.1:18087/api/runs/run-1/retry?source=ui",
    );
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).has("host")).toBe(false);
    expect(response.status).toBe(202);
  });
});
