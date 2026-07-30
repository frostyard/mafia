import Link from "next/link";
import type { Run } from "@/lib/types";
import { formatTimestamp, runStateTone, type RunState } from "@/lib/workflow-state";

export function StateBadge({ state }: { state: RunState }) {
  return (
    <span className={`state-badge state-${state} tone-${runStateTone(state)}`} data-tone={runStateTone(state)}>
      {state.replaceAll("_", " ")}
    </span>
  );
}

export function RunCards({ runs }: { runs: Run[] }) {
  if (runs.length === 0) {
    return (
      <section className="empty-state ph-card">
        <p className="eyebrow">No work in flight</p>
        <h2>Start an engineering workflow.</h2>
        <p className="muted">
          Build from a requirement or run an independent pull request review.
        </p>
        <Link className="button" href="/runs/new">
          Create your first run
        </Link>
      </section>
    );
  }

  return (
    <section className="ph-card ph-table-card run-table-card">
      <div className="ph-table-toolbar">
        <h2>Workflow runs</h2>
        <span>{runs.length} recorded · newest first</span>
      </div>
      <div className="table-scroll">
        <table className="ph-table">
          <thead>
            <tr>
              <th>Repository</th>
              <th>Input</th>
              <th>Models</th>
              <th>State</th>
              <th>Updated</th>
              <th><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>
                  <div className="ph-name">
                    <strong>{run.repository.owner}/{run.repository.name}</strong>
                    <small>
                      {run.workflow_type === "pull_request_review"
                        ? "PR review"
                        : "Specification delivery"}{" "}
                      · {run.id.slice(0, 8)} · revision {run.version}
                    </small>
                  </div>
                </td>
                <td className="run-requirement">
                  {run.workflow_type === "pull_request_review"
                    ? `Pull request #${run.pull_request_number ?? "unavailable"}`
                    : run.requirement_type === "issue"
                    ? `Issue #${run.issue_number ?? "unavailable"}`
                    : run.requirement_text || "Written requirement"}
                </td>
                <td>
                  <div className="ph-name">
                    <strong>{run.primary_model}</strong>
                    <small>
                      {run.workflow_type === "pull_request_review"
                        ? `adjudicates with ${run.reviewer_model}`
                        : `reviewed by ${run.reviewer_model}`}
                    </small>
                  </div>
                </td>
                <td><StateBadge state={run.state} /></td>
                <td><time className="ph-version" dateTime={run.updated_at}>{formatTimestamp(run.updated_at)}</time></td>
                <td>
                  <div className="ph-actions">
                    <Link className="button button-small button-secondary" href={`/runs/${run.id}`}>
                      Open
                    </Link>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
