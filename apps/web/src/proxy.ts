import { NextRequest, NextResponse } from "next/server";
import {
  githubAuthEnabled,
  isPublicAuthPath,
  safeReturnPath,
  type AuthenticatedUser,
} from "@/lib/auth";

async function currentUser(request: NextRequest): Promise<AuthenticatedUser | undefined> {
  const apiUrl = process.env.MAFIA_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${apiUrl.replace(/\/+$/, "")}/auth/session`, {
      cache: "no-store",
      headers: { cookie: request.headers.get("cookie") ?? "" },
    });
    if (!response.ok) return undefined;
    return (await response.json()) as AuthenticatedUser;
  } catch {
    return undefined;
  }
}

export async function proxy(request: NextRequest): Promise<NextResponse> {
  if (
    !githubAuthEnabled() ||
    isPublicAuthPath(request.nextUrl.pathname)
  ) {
    return NextResponse.next();
  }
  const user = await currentUser(request);
  if (user) {
    const requestHeaders = new Headers(request.headers);
    for (const header of [
      "x-mafia-github-avatar",
      "x-mafia-github-login",
      "x-mafia-github-session-expires",
      "x-mafia-github-user-id",
    ]) {
      requestHeaders.delete(header);
    }
    requestHeaders.set(
      "x-mafia-github-user-id",
      String(user.github_user_id),
    );
    requestHeaders.set("x-mafia-github-login", user.login);
    requestHeaders.set(
      "x-mafia-github-session-expires",
      String(user.expires_at),
    );
    if (user.avatar_url) {
      requestHeaders.set("x-mafia-github-avatar", user.avatar_url);
    }
    return NextResponse.next({ request: { headers: requestHeaders } });
  }
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json(
      {
        detail: {
          code: "authentication_required",
          message: "Sign in with GitHub to continue",
        },
      },
      { status: 401 },
    );
  }
  const login = new URL("/auth/login", request.url);
  login.searchParams.set(
    "return_to",
    safeReturnPath(request.nextUrl.pathname, request.nextUrl.search),
  );
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
