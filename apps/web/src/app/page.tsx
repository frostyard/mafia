import Link from "next/link";
import { RunCards } from "@/components/run-cards";
import { getRuns } from "@/lib/api";
import { isDecisionState, isTerminalState } from "@/lib/workflow-state";

export default async function DashboardPage() {
  let runs;
  try {
    runs = await getRuns();
  } catch {}
  const activeRuns = runs?.filter((run) =>
    !isTerminalState(run.state),
  ).length ?? 0;
  const decisions = runs?.filter((run) => isDecisionState(run.state)).length ?? 0;
  const completed = runs?.filter((run) => run.state === "completed").length ?? 0;

  return (
    <>
      <header className="ph-topbar">
        <div>
          <p className="ph-eyebrow">Workflows · local</p>
          <h1>Engineering runs</h1>
        </div>
        <div className="ph-topbar-actions">
          <span className="ph-live"><span aria-hidden="true" />Local orchestration</span>
          <Link className="button button-small" href="/runs/new">New run</Link>
        </div>
      </header>
      {runs ? (
        <>
          <section className="ph-stats" aria-label="Workflow summary">
            <div className="ph-stat"><span>Total runs</span><strong>{runs.length}</strong><small>durable workflow records</small></div>
            <div className="ph-stat"><span>Active</span><strong>{activeRuns}</strong><small>in progress or awaiting input</small></div>
            <div className="ph-stat"><span>Decisions</span><strong>{decisions}</strong><small>operator action required</small></div>
            <div className="ph-stat"><span>Completed</span><strong>{completed}</strong><small>all phases delivered</small></div>
          </section>
          <RunCards runs={runs} />
        </>
      ) : (
        <section className="empty-state ph-card" role="status">
          <p className="eyebrow">Connection unavailable</p>
          <h2>MAFIA is not ready yet.</h2>
          <p className="muted">Start the API service and refresh this page to load workflow runs.</p>
        </section>
      )}
    </>
  );
}
