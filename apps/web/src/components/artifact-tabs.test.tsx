import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ArtifactTabs } from "@/components/artifact-tabs";
import { formatTimestamp } from "@/lib/workflow-state";

describe("ArtifactTabs", () => {
  it("renders artifact text as content rather than HTML", () => {
    render(
      <ArtifactTabs
        artifacts={[
          {
            id: "artifact-1",
            kind: "specification",
            schema_version: 1,
            revision: 1,
            structured_data: {},
            rendered_markdown: "# Specification\n\n<script>unsafe</script>",
            model: "claude-opus-4.8",
            source_snapshot_id: null,
            created_at: "2026-07-28T00:00:00Z",
          },
        ]}
      />,
    );

    expect(screen.getByText("<script>unsafe</script>")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Review" }));
    expect(screen.getByText("No review artifact is available yet.")).toBeTruthy();
  });

  it("shows independent and consolidated pull request reviews", () => {
    render(
      <ArtifactTabs
        artifacts={[
          {
            id: "review-1",
            kind: "pull_request_review",
            schema_version: 1,
            revision: 1,
            structured_data: {},
            rendered_markdown: "# Opus review",
            model: "claude-opus-4.8",
            source_snapshot_id: "snapshot-1",
            created_at: "2026-07-28T00:00:00Z",
          },
        ]}
        workflowType="pull_request_review"
      />,
    );

    expect(screen.getByText("Opus review")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Consolidated review" }));
    expect(
      screen.getByText("No pull request review consolidated artifact is available yet."),
    ).toBeTruthy();
  });

  it("shows bounded implementation review artifacts", () => {
    render(
      <ArtifactTabs
        artifacts={[
          {
            id: "implementation-review-1",
            kind: "implementation_review",
            schema_version: 1,
            revision: 1,
            structured_data: {},
            rendered_markdown: "# Implementation review cycle 1",
            model: "gpt-5.6-sol",
            source_snapshot_id: null,
            created_at: "2026-07-29T00:00:00Z",
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Implementation review" }));
    expect(screen.getByText("Implementation review cycle 1")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Implementation decision" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Remediation" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Closure verification" })).toBeTruthy();
  });

  it("formats artifact timestamps in UTC", () => {
    const originalTimeZone = process.env.TZ;
    try {
      render(
        <ArtifactTabs
          artifacts={[{
            id: "artifact-utc", kind: "specification", schema_version: 1, revision: 1,
            structured_data: {}, rendered_markdown: "# Specification", model: "model",
            source_snapshot_id: null, created_at: "2026-07-30T12:00:00Z",
          }]}
        />,
      );

      const utcTimestamp = formatTimestamp("2026-07-30T12:00:00Z");
      process.env.TZ = "America/Los_Angeles";
      expect(formatTimestamp("2026-07-30T12:00:00Z")).toBe(utcTimestamp);
      expect(utcTimestamp).toBe("Jul 30, 2026, 12:00 PM");
      expect(screen.getByText("Jul 30, 2026, 12:00 PM")).toBeTruthy();
    } finally {
      if (originalTimeZone === undefined) delete process.env.TZ;
      else process.env.TZ = originalTimeZone;
    }
  });
});
