import { describe, expect, it } from "vitest";
import {
  eventLabel,
  humanizeIdentifier,
  operationLabel,
} from "@/lib/activity-labels";

describe("activity labels", () => {
  it("uses action-oriented labels for known operations", () => {
    expect(operationLabel("model.plan_generation")).toBe("Generating plan");
    expect(operationLabel("source.grounding")).toBe("Grounding source");
  });

  it("humanizes durable event identifiers", () => {
    expect(eventLabel("plan.grounding_started")).toBe(
      "Plan grounding started",
    );
    expect(eventLabel("pull_request_review.post_retry_ready")).toBe(
      "Pull request review post retry ready",
    );
  });

  it("provides a readable fallback for new identifiers", () => {
    expect(humanizeIdentifier("model.future_operation")).toBe(
      "Model future operation",
    );
  });
});
