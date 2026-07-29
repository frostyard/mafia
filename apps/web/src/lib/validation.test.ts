import { describe, expect, it } from "vitest";
import { reviewerFor } from "@/lib/models";
import { validateRunForm } from "@/lib/validation";

describe("run form validation", () => {
  it("maps an issue source to the API payload", () => {
    const result = validateRunForm({
      workflowType: "specification",
      repository: " github.com/acme/rocket ",
      primaryModel: "claude-opus-4.8",
      requirementMode: "issue",
      issueNumber: "42",
      requirementText: "",
      pullRequestNumber: "",
    });

    expect(result).toEqual({
      ok: true,
      data: {
        workflow_type: "specification",
        repository: "github.com/acme/rocket",
        primary_model: "claude-opus-4.8",
        issue_number: 42,
      },
    });
  });

  it("accepts a full GitHub issue URL", () => {
    const result = validateRunForm({
      workflowType: "specification",
      repository: "acme/rocket",
      primaryModel: "claude-opus-4.8",
      requirementMode: "issue",
      issueNumber: "https://github.com/acme/rocket/issues/77",
      requirementText: "",
      pullRequestNumber: "",
    });

    expect(result).toMatchObject({ ok: true, data: { issue_number: 77 } });
  });

  it("requires a requirement for text runs", () => {
    const result = validateRunForm({
      workflowType: "specification",
      repository: "acme/rocket",
      primaryModel: "gpt-5.6-sol",
      requirementMode: "text",
      issueNumber: "",
      requirementText: "   ",
      pullRequestNumber: "",
    });

    expect(result).toEqual({
      ok: false,
      errors: { requirementText: "Describe the requirement to implement." },
    });
  });

  it("selects the opposite model as reviewer", () => {
    const pairs = [
      {
        primary_model: "claude-opus-4.8",
        reviewer_model: "gpt-5.6-sol",
      },
      {
        primary_model: "gpt-5.6-sol",
        reviewer_model: "claude-opus-4.8",
      },
    ];
    expect(reviewerFor("claude-opus-4.8", pairs)).toBe("GPT-5.6 Sol");
    expect(reviewerFor("gpt-5.6-sol", pairs)).toBe("Claude Opus 4.8");
  });

  it("maps a pull request URL to a review run", () => {
    const result = validateRunForm({
      workflowType: "pull_request_review",
      repository: "acme/rocket",
      primaryModel: "gpt-5.6-sol",
      requirementMode: "issue",
      issueNumber: "",
      requirementText: "",
      pullRequestNumber: "https://github.com/acme/rocket/pull/81",
    });

    expect(result).toEqual({
      ok: true,
      data: {
        workflow_type: "pull_request_review",
        repository: "acme/rocket",
        primary_model: "gpt-5.6-sol",
        pull_request_number: 81,
      },
    });
  });
});
