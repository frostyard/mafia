import type { Phase } from "@/lib/types";
import { phaseStateTone } from "@/lib/workflow-state";

function safeExternalUrl(value: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

function shortSha(value: string | null): string {
  return value ? value.slice(0, 10) : "Not available";
}

export function PhaseBoard({ phases }: { phases: Phase[] }) {
  return (
    <section className="phases-panel ph-card" aria-labelledby="phases-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Execution</p>
          <h2 id="phases-heading">Phase board</h2>
        </div>
        <span className="muted">{phases.length} planned</span>
      </div>
      {phases.length ? (
        <ol className="phase-board">
          {phases
            .slice()
            .sort((left, right) => left.ordinal - right.ordinal)
            .map((phase) => {
              const prUrl = safeExternalUrl(phase.pr_url);
              return (
                <li className="phase-card" key={phase.id}>
                  <div className="phase-heading">
                    <span className="phase-ordinal">Phase {phase.ordinal}</span>
                    <span className={`state-badge phase-${phase.status} tone-${phaseStateTone(phase.status)}`}>{phase.status.replaceAll("_", " ")}</span>
                  </div>
                  <h3>{phase.title}</h3>
                  <p>{phase.objective}</p>
                  <dl className="phase-meta">
                    <div>
                      <dt>Dependencies</dt>
                      <dd>{phase.dependencies.length ? phase.dependencies.join(", ") : "None"}</dd>
                    </div>
                    <div>
                      <dt>Plan revision</dt>
                      <dd>{phase.plan_revision}</dd>
                    </div>
                    <div>
                      <dt>Source</dt>
                      <dd><code>{shortSha(phase.source_sha)}</code></dd>
                    </div>
                    <div>
                      <dt>Branch</dt>
                      <dd>{phase.branch_name ?? "Not created"}</dd>
                    </div>
                    <div>
                      <dt>Commit</dt>
                      <dd><code>{shortSha(phase.commit_sha)}</code></dd>
                    </div>
                    <div>
                      <dt>Merge</dt>
                      <dd><code>{shortSha(phase.merge_sha)}</code></dd>
                    </div>
                  </dl>
                  {prUrl ? (
                    <a className="text-link" href={prUrl} rel="noreferrer" target="_blank">
                      Open PR #{phase.pr_number ?? "unknown"} <span aria-hidden="true">-&gt;</span>
                    </a>
                  ) : (
                    <p className="placeholder">No pull request has been opened for this phase.</p>
                  )}
                  <details className="phase-details">
                    <summary>Phase details</summary>
                    <pre>{JSON.stringify(phase.details, null, 2)}</pre>
                  </details>
                </li>
              );
            })}
        </ol>
      ) : (
        <p className="placeholder">Phases will appear after the reviewed plan is accepted.</p>
      )}
    </section>
  );
}
