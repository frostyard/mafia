import Link from "next/link";
import { notFound } from "next/navigation";
import { ArtifactTabs } from "@/components/artifact-tabs";
import { EvidenceDrawer } from "@/components/evidence-drawer";
import { PhaseBoard } from "@/components/phase-board";
import { RefreshPrStatus } from "@/components/refresh-pr-status";
import { StateBadge } from "@/components/run-cards";
import { StageTimeline } from "@/components/stage-timeline";
import { VisibilityRail } from "@/components/visibility-rail";
import { WorkflowPanel } from "@/components/workflow-panel";
import { getEvidence, getRun, getRunActivity } from "@/lib/api";
import type { ApiError, Evidence, RunActivity, RunDetail } from "@/lib/types";

function DetailCard({
  title,
  children,
}: Readonly<{ title: string; children: React.ReactNode }>) {
  return (
    <section className="detail-card ph-card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function RunDetailView({
  run,
  evidence,
  evidenceError,
  activity,
}: {
  run: RunDetail;
  evidence: Evidence[];
  evidenceError?: string;
  activity: RunActivity;
}) {
  const repositoryName = `${run.repository.owner}/${run.repository.name}`;
  const isPullRequestReview = run.workflow_type === "pull_request_review";
  const consolidatedReview = run.artifacts
    .filter((artifact) => artifact.kind === "pull_request_review_consolidated")
    .sort((left, right) => right.revision - left.revision)[0];
  const findings = Array.isArray(consolidatedReview?.structured_data.findings)
    ? consolidatedReview.structured_data.findings.length
    : 0;
  return (
    <>
      <header className="ph-topbar run-detail-topbar">
        <div>
          <p className="ph-eyebrow">
            {isPullRequestReview ? "Pull request review" : "Delivery workflow"} ·{" "}
            {run.id.slice(0, 8)}
          </p>
          <h1>{repositoryName}</h1>
        </div>
        <div className="ph-topbar-actions">
          <StateBadge state={run.state} />
          <Link className="button button-small button-secondary" href="/">
            All runs
          </Link>
        </div>
      </header>

      <div className="run-workspace-layout">
        <main className="run-workspace-main">
        <section className="ph-stats run-stats" aria-label="Run summary">
          {isPullRequestReview ? (
            <>
              <div className="ph-stat">
                <span>Pull request</span>
                <strong>#{run.pull_request_number ?? "—"}</strong>
                <small>review target</small>
              </div>
              <div className="ph-stat">
                <span>Review revision</span>
                <strong>{run.active_review_revision ?? "—"}</strong>
                <small>consolidated artifact</small>
              </div>
              <div className="ph-stat">
                <span>Review models</span>
                <strong>2</strong>
                <small>independent passes</small>
              </div>
              <div className="ph-stat">
                <span>Findings</span>
                <strong>{findings}</strong>
                <small>after adjudication</small>
              </div>
            </>
          ) : (
            <>
              <div className="ph-stat">
                <span>Version</span>
                <strong>{run.version}</strong>
                <small>optimistic state revision</small>
              </div>
              <div className="ph-stat">
                <span>Specification</span>
                <strong>{run.active_spec_revision ?? "—"}</strong>
                <small>active artifact revision</small>
              </div>
              <div className="ph-stat">
                <span>Plan</span>
                <strong>{run.active_plan_revision ?? "—"}</strong>
                <small>reviewed artifact revision</small>
              </div>
              <div className="ph-stat">
                <span>Phases</span>
                <strong>{run.phases.length}</strong>
                <small>PR-sized delivery units</small>
              </div>
            </>
          )}
        </section>

        <section className="progress-panel ph-card">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Current stage</p>
              <h2>{run.state.replaceAll("_", " ")}</h2>
            </div>
            <div className="run-actions">
              <span className="muted">Version {run.version}</span>
              {run.state === "waiting_for_merge" ? <RefreshPrStatus runId={run.id} /> : null}
            </div>
          </div>
          <StageTimeline activity={activity.events} state={run.state} workflowType={run.workflow_type} />
        </section>

        {run.failure_message && run.state === "failed" ? (
          <section className="failure-panel" role="alert">
            <strong>{run.failure_code ?? "Workflow error"}</strong>
            <p>{run.failure_message}</p>
          </section>
        ) : null}

        <div className="detail-grid">
          <DetailCard title={isPullRequestReview ? "Review target" : "Requirement"}>
            {isPullRequestReview ? (
              <p>GitHub pull request #{run.pull_request_number ?? "unavailable"}</p>
            ) : run.requirement_type === "issue" ? (
              <p>GitHub issue #{run.issue_number ?? "unavailable"}</p>
            ) : (
              <p>{run.requirement_text || "The requirement text is not available."}</p>
            )}
          </DetailCard>
          <DetailCard title="Models">
            <dl className="stacked-meta">
              <div>
                <dt>{isPullRequestReview ? "Adjudicator" : "Primary"}</dt>
                <dd>{run.primary_model}</dd>
              </div>
              <div>
                <dt>{isPullRequestReview ? "Independent peer" : "Reviewer"}</dt>
                <dd>{run.reviewer_model}</dd>
              </div>
            </dl>
          </DetailCard>
        </div>
        <ArtifactTabs
          artifacts={run.artifacts}
          workflowType={run.workflow_type}
        />
        <EvidenceDrawer evidence={evidence} error={evidenceError} />
        {!isPullRequestReview ? <PhaseBoard phases={run.phases} /> : null}
        <WorkflowPanel run={run} />
        </main>
        <VisibilityRail
          initialActivity={activity}
          runId={run.id}
          workflowType={run.workflow_type}
        />
      </div>
    </>
  );
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let run: RunDetail | undefined;
  let evidence: Evidence[] = [];
  let activity: RunActivity | undefined;
  let requestError: ApiError | undefined;
  let evidenceError: string | undefined;
  const evidenceRequest = getEvidence(id).then(
    (loadedEvidence) => ({ evidence: loadedEvidence, error: undefined }),
    () => ({ evidence: [], error: "Source evidence is unavailable." }),
  );
  try {
    [run, activity] = await Promise.all([
      getRun(id),
      getRunActivity(id),
    ]);
    ({ evidence, error: evidenceError } = await evidenceRequest);
  } catch (error) {
    requestError = error as ApiError;
  }
  if (run && activity) {
    return (
      <RunDetailView
        activity={activity}
        evidence={evidence}
        evidenceError={evidenceError}
        run={run}
      />
    );
  }
  if (requestError?.code === "run_not_found") {
    notFound();
  }
  return (
    <section className="empty-state ph-card" role="status">
      <p className="eyebrow">Run unavailable</p>
      <h1>We could not load this run.</h1>
      <p className="muted">{requestError?.message ?? "Try again after the API is available."}</p>
      <Link className="button" href="/">Return to runs</Link>
    </section>
  );
}
