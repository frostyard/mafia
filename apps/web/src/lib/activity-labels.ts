const operationLabels: Record<string, string> = {
  "artifact.persistence": "Saving artifact",
  "citation.validation": "Validating citations",
  "diff.validation": "Validating changes",
  "environment.cleanup": "Cleaning up environment",
  "environment.prepare": "Preparing environment",
  "environment.validation": "Running validation",
  "git.commit": "Creating commit",
  "git.push": "Pushing branch",
  "github.pull_request": "Creating pull request",
  "github.pull_request_comment": "Posting review comment",
  "model.adversarial_review": "Reviewing plan",
  "model.phase_implementation": "Implementing phase",
  "model.plan_adjudication": "Adjudicating plan",
  "model.plan_generation": "Generating plan",
  "model.pull_request_review": "Reviewing pull request",
  "model.pull_request_review_adjudication": "Adjudicating pull request review",
  "model.specification": "Generating specification",
  "model.structured_output": "Generating structured result",
  "sandbox.validation": "Running sandbox validation",
  "source.grounding": "Grounding source",
  "source.refresh": "Refreshing source",
  "worktree.create": "Creating worktree",
};

export function humanizeIdentifier(value: string): string {
  const words = value
    .trim()
    .split(/[._-]+/)
    .filter(Boolean)
    .join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : value;
}

export function operationLabel(operationType: string): string {
  return operationLabels[operationType] ?? humanizeIdentifier(operationType);
}

export function eventLabel(eventType: string): string {
  return humanizeIdentifier(eventType);
}
