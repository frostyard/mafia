"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { AuthenticatedUser } from "@/lib/auth";

export function Header({ user }: { user?: AuthenticatedUser }) {
  const pathname = usePathname();
  const runsActive = pathname !== "/runs/new";
  return (
    <aside className="ph-sidebar">
      <Link className="ph-brand" href="/">
        <span className="flake" aria-hidden="true">❄</span>
        <span>
          <strong>MAFIA</strong>
          <small>engineering agent</small>
        </span>
      </Link>
      <nav className="ph-nav" aria-label="Primary navigation">
        <p className="ph-nav-group">Workflows</p>
        <Link className={`ph-nav-link${runsActive ? " active" : ""}`} href="/">
          <span className="ph-nav-num">01</span>
          Runs
        </Link>
        <Link className={`ph-nav-link${runsActive ? "" : " active"}`} href="/runs/new">
          <span className="ph-nav-num">02</span>
          New run
        </Link>
      </nav>
      <div className="ph-side-foot">
        <span className="ph-avatar" aria-hidden="true">
          {user ? user.login.slice(0, 2).toUpperCase() : "GH"}
        </span>
        <span className="ph-side-user">
          <strong>{user?.login ?? "Local operator"}</strong>
          <small>{user ? `GitHub ID ${user.github_user_id}` : "GitHub Copilot"}</small>
        </span>
        {user ? (
          <form action="/auth/logout" method="post">
            <button className="ph-sign-out" type="submit">
              Sign out
            </button>
          </form>
        ) : null}
      </div>
    </aside>
  );
}
