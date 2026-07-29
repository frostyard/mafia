import type { PrimaryModel, RunCreate, WorkflowType } from "@/lib/types";

export type RequirementMode = "issue" | "text";

export interface RunFormValues {
  workflowType: WorkflowType;
  repository: string;
  primaryModel: PrimaryModel;
  requirementMode: RequirementMode;
  issueNumber: string;
  requirementText: string;
  pullRequestNumber: string;
}

export type ValidationResult =
  | { ok: true; data: RunCreate }
  | { ok: false; errors: Partial<Record<keyof RunFormValues, string>> };

export function parseIssueNumber(reference: string): number | undefined {
  const match = reference
    .trim()
    .match(/^(?:#?(\d+)|https:\/\/github\.com\/[^/]+\/[^/]+\/issues\/(\d+)\/?)$/i);
  if (!match) return undefined;
  const number = Number(match[1] ?? match[2]);
  return Number.isSafeInteger(number) && number > 0 ? number : undefined;
}

export function parsePullRequestNumber(reference: string): number | undefined {
  const match = reference
    .trim()
    .match(/^(?:#?(\d+)|https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/(\d+)\/?)$/i);
  if (!match) return undefined;
  const number = Number(match[1] ?? match[2]);
  return Number.isSafeInteger(number) && number > 0 ? number : undefined;
}

export function validateRunForm(values: RunFormValues): ValidationResult {
  const repository = values.repository.trim();
  const errors: Partial<Record<keyof RunFormValues, string>> = {};

  if (repository.length < 3) {
    errors.repository = "Enter a repository URL or owner/repository.";
  }

  if (values.workflowType === "pull_request_review") {
    const pullRequestNumber = parsePullRequestNumber(values.pullRequestNumber);
    if (pullRequestNumber === undefined) {
      errors.pullRequestNumber =
        "Enter a pull request number, #number, or full GitHub pull request URL.";
    }
    if (Object.keys(errors).length > 0) {
      return { ok: false, errors };
    }
    return {
      ok: true,
      data: {
        workflow_type: "pull_request_review",
        repository,
        primary_model: values.primaryModel,
        pull_request_number: pullRequestNumber!,
      },
    };
  }

  if (values.requirementMode === "issue") {
    const issueNumber = parseIssueNumber(values.issueNumber);
    if (issueNumber === undefined) {
      errors.issueNumber = "Enter an issue number, #number, or full GitHub issue URL.";
    }
    if (Object.keys(errors).length > 0) {
      return { ok: false, errors };
    }
    return {
      ok: true,
      data: {
        workflow_type: "specification",
        repository,
        primary_model: values.primaryModel,
        issue_number: issueNumber!,
      },
    };
  }

  const requirementText = values.requirementText.trim();
  if (!requirementText) {
    errors.requirementText = "Describe the requirement to implement.";
  }
  if (Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }
  return {
    ok: true,
    data: {
      workflow_type: "specification",
      repository,
      primary_model: values.primaryModel,
      requirement_text: requirementText,
    },
  };
}
