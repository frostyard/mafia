export interface AuthenticatedUser {
  github_user_id: number;
  login: string;
  avatar_url: string | null;
  expires_at: number;
}

const publicAuthPaths = new Set([
  "/auth/callback",
  "/auth/forward",
  "/auth/login",
  "/auth/logout",
]);

export function githubAuthEnabled(): boolean {
  return process.env.MAFIA_AUTH_MODE === "github";
}

export function isPublicAuthPath(pathname: string): boolean {
  return publicAuthPaths.has(pathname);
}

export function safeReturnPath(pathname: string, search: string): string {
  const value = `${pathname}${search}`;
  return value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.startsWith("/\\") &&
    !value.includes("\\")
    ? value
    : "/";
}

export function userFromHeaders(headers: Headers): AuthenticatedUser | undefined {
  const userId = Number(headers.get("x-mafia-github-user-id"));
  const login = headers.get("x-mafia-github-login");
  const expiresAt = Number(headers.get("x-mafia-github-session-expires"));
  if (
    !Number.isSafeInteger(userId) ||
    userId <= 0 ||
    !login ||
    !Number.isSafeInteger(expiresAt)
  ) {
    return undefined;
  }
  return {
    github_user_id: userId,
    login,
    avatar_url: headers.get("x-mafia-github-avatar"),
    expires_at: expiresAt,
  };
}
