"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function Header() {
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
        <span className="ph-avatar" aria-hidden="true">GH</span>
        <span className="ph-side-user">
          <strong>Local operator</strong>
          <small>GitHub Copilot</small>
        </span>
      </div>
    </aside>
  );
}
