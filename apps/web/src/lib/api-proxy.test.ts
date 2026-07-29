import { afterEach, describe, expect, it, vi } from "vitest";
import { proxyApiRequest } from "@/lib/api-proxy";

describe("proxyApiRequest", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.MAFIA_API_URL;
    delete process.env.MAFIA_INTERNAL_SECRET;
  });

  it("uses the runtime API URL and preserves request details", async () => {
    process.env.MAFIA_API_URL = "http://127.0.0.1:18087";
    process.env.MAFIA_INTERNAL_SECRET = "trusted-internal-secret";
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
        headers: {
          "content-type": "application/json",
          host: "localhost",
          "x-mafia-internal-secret": "untrusted-client-value",
        },
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
    expect(
      new Headers(init?.headers).get("x-mafia-internal-secret"),
    ).toBe("trusted-internal-secret");
    expect(response.status).toBe(202);
  });

  it("preserves multiple OAuth response cookies", async () => {
    const headers = new Headers();
    headers.append("set-cookie", "mafia_oauth_flow=; Max-Age=0; Path=/");
    headers.append("set-cookie", "mafia_session=signed; HttpOnly; Secure; Path=/");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { headers, status: 303 })),
    );

    const response = await proxyApiRequest(
      new Request("http://127.0.0.1:3000/auth/callback"),
      "/auth/callback",
    );

    expect(response.headers.getSetCookie()).toHaveLength(2);
  });
});
