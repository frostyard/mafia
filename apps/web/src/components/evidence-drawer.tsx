import type { Evidence } from "@/lib/types";

export function EvidenceDrawer({
  evidence,
  error,
}: {
  evidence: Evidence[];
  error?: string;
}) {
  if (!evidence.length && !error) return null;
  return (
    <details className="ph-card artifact-data">
      <summary>Source evidence ({evidence.length})</summary>
      {error ? <p className="form-alert" role="alert">{error}</p> : null}
      <div className="artifact-list">
        {evidence.map((item) => (
          <article className="artifact-card" key={item.id}>
            <strong>
              {item.path_or_url}
              {item.line_start !== null
                ? `:${item.line_start}${item.line_end != null && item.line_end !== item.line_start ? `-${item.line_end}` : ""}`
                : ""}
            </strong>
            <p className="muted">
              {item.kind} at {item.source_sha.slice(0, 12)}
            </p>
            {typeof item.detail.claim === "string" ? <p>{item.detail.claim}</p> : null}
            <code>{item.excerpt_hash.slice(0, 16)}</code>
          </article>
        ))}
      </div>
    </details>
  );
}
