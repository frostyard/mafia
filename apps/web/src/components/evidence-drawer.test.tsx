import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvidenceDrawer } from "@/components/evidence-drawer";
import type { Evidence } from "@/lib/types";

const evidence: Evidence = {
  id: "evidence-1",
  snapshot_id: "snapshot-1",
  source_sha: "1234567890abcdef",
  kind: "source",
  path_or_url: "file.py",
  line_start: 5,
  line_end: null,
  excerpt_hash: "abcdef1234567890",
  detail: {},
  created_at: "2026-01-01T00:00:00Z",
};

describe("EvidenceDrawer", () => {
  it("does not render a null line range suffix", () => {
    render(<EvidenceDrawer evidence={[evidence]} />);

    expect(screen.getByText("file.py:5")).toBeTruthy();
    expect(screen.queryByText("file.py:5-null")).toBeNull();
  });

  it("renders its localized error when evidence is unavailable", () => {
    const { container } = render(
      <EvidenceDrawer evidence={[]} error="Source evidence is unavailable." />,
    );

    expect(screen.getByRole("alert").textContent).toContain(
      "Source evidence is unavailable.",
    );
    expect(screen.getByText("Source evidence unavailable (0)")).toBeTruthy();
    expect(container.querySelector("details")?.open).toBe(true);
  });

  it("remains collapsed when evidence loads without an error", () => {
    const { container } = render(<EvidenceDrawer evidence={[evidence]} />);

    expect(container.querySelector("details")?.open).toBe(false);
  });
});
