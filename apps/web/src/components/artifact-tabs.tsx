"use client";

import { useMemo, useState } from "react";
import type { Artifact, ArtifactKind, WorkflowType } from "@/lib/types";

const specificationTabs: { kind: ArtifactKind; label: string }[] = [
  { kind: "specification", label: "Specification" },
  { kind: "plan", label: "Plan" },
  { kind: "review", label: "Review" },
  { kind: "review_ledger", label: "Review ledger" },
];

const pullRequestReviewTabs: { kind: ArtifactKind; label: string }[] = [
  { kind: "pull_request_review", label: "Model reviews" },
  {
    kind: "pull_request_review_consolidated",
    label: "Consolidated review",
  },
];

function formatStructuredData(data: Record<string, unknown>): string {
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return "Structured artifact data is unavailable.";
  }
}

export function MarkdownLike({ content }: { content: string }) {
  const lines = content.split(/\r?\n/);
  return (
    <div className="markdown-like">
      {lines.map((line, index) => {
        const key = `${index}-${line.slice(0, 24)}`;
        if (line.startsWith("### ")) return <h4 key={key}>{line.slice(4)}</h4>;
        if (line.startsWith("## ")) return <h3 key={key}>{line.slice(3)}</h3>;
        if (line.startsWith("# ")) return <h2 key={key}>{line.slice(2)}</h2>;
        if (/^[-*] /.test(line)) return <p className="markdown-list-item" key={key}>{line.slice(2)}</p>;
        if (/^\d+\. /.test(line)) return <p className="markdown-list-item" key={key}>{line}</p>;
        if (!line.trim()) return <div className="markdown-spacer" key={key} />;
        return <p key={key}>{line}</p>;
      })}
    </div>
  );
}

function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const content = artifact.rendered_markdown || formatStructuredData(artifact.structured_data);
  return (
    <article className="artifact-card">
      <header>
        <div>
          <span className="artifact-revision">Revision {artifact.revision}</span>
          <span className="artifact-model">{artifact.model}</span>
        </div>
        <time dateTime={artifact.created_at}>
          {new Date(artifact.created_at).toLocaleString("en")}
        </time>
      </header>
      <MarkdownLike content={content} />
      <details className="artifact-data">
        <summary>Structured data</summary>
        <pre>{formatStructuredData(artifact.structured_data)}</pre>
      </details>
    </article>
  );
}

export function ArtifactTabs({
  artifacts,
  workflowType = "specification",
}: {
  artifacts: Artifact[];
  workflowType?: WorkflowType;
}) {
  const artifactTabs =
    workflowType === "pull_request_review"
      ? pullRequestReviewTabs
      : specificationTabs;
  const [activeKind, setActiveKind] = useState<ArtifactKind>(
    artifactTabs[0].kind,
  );
  const currentArtifacts = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === activeKind),
    [activeKind, artifacts],
  );

  return (
    <section className="artifacts-panel ph-card" aria-labelledby="artifacts-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Evidence trail</p>
          <h2 id="artifacts-heading">Artifacts</h2>
        </div>
        <span className="muted">{artifacts.length} available</span>
      </div>
      <div className="artifact-tabs" role="tablist" aria-label="Workflow artifacts">
        {artifactTabs.map((tab) => {
          const isActive = activeKind === tab.kind;
          return (
            <button
              aria-controls={`artifact-panel-${tab.kind}`}
              aria-selected={isActive}
              className={isActive ? "active" : ""}
              id={`artifact-tab-${tab.kind}`}
              key={tab.kind}
              onClick={() => setActiveKind(tab.kind)}
              role="tab"
              type="button"
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      <div
        aria-labelledby={`artifact-tab-${activeKind}`}
        id={`artifact-panel-${activeKind}`}
        role="tabpanel"
      >
        {currentArtifacts.length ? (
          <div className="artifact-list">
            {currentArtifacts
              .slice()
              .sort((left, right) => right.revision - left.revision)
              .map((artifact) => <ArtifactCard artifact={artifact} key={artifact.id} />)}
          </div>
        ) : (
          <p className="placeholder">No {activeKind.replaceAll("_", " ")} artifact is available yet.</p>
        )}
      </div>
    </section>
  );
}
