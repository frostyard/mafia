import { afterEach, describe, expect, it } from "vitest";
import {
  githubAuthEnabled,
  isPublicAuthPath,
  safeReturnPath,
  userFromHeaders,
} from "@/lib/auth";

describe("web authentication helpers", () => {
  afterEach(() => {
    delete process.env.MAFIA_AUTH_MODE;
  });

  it("keeps authentication disabled unless explicitly enabled", () => {
    expect(githubAuthEnabled()).toBe(false);
    process.env.MAFIA_AUTH_MODE = "github";
    expect(githubAuthEnabled()).toBe(true);
  });

  it("allows only OAuth flow endpoints through the public guard", () => {
    expect(isPublicAuthPath("/auth/login")).toBe(true);
    expect(isPublicAuthPath("/auth/callback")).toBe(true);
    expect(isPublicAuthPath("/auth/forward")).toBe(true);
    expect(isPublicAuthPath("/auth/session")).toBe(false);
  });

  it("rejects protocol-relative return targets", () => {
    expect(safeReturnPath("/runs/new", "?from=login")).toBe(
      "/runs/new?from=login",
    );
    expect(safeReturnPath("//attacker.example", "")).toBe("/");
    expect(safeReturnPath("/\\attacker.example", "")).toBe("/");
  });

  it("parses authenticated identity from trusted request headers", () => {
    const headers = new Headers({
      "x-mafia-github-user-id": "37492",
      "x-mafia-github-login": "bketelsen",
      "x-mafia-github-session-expires": "2000000000",
    });

    expect(userFromHeaders(headers)?.login).toBe("bketelsen");
  });
});
