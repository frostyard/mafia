# Workflow lifecycle

MAFIA supports specification delivery and ad-hoc pull request review as separate durable run types.

## Specification delivery

Create a run with an `owner/repository`, a GitHub issue number or URL or written requirement, and a primary
model. Open the run and select **Start workflow**.

The delivery workflow:

1. Grounds and generates a structured specification.
2. Pauses for the user to accept the specification or refine it with feedback.
3. Grounds an implementation plan in the current repository source.
4. Sends the plan to the competing model for adversarial review.
5. Has the primary model adjudicate every finding and produce a revised, PR-sized phased plan.
6. Pauses for the user to accept or refine the reviewed plan.
7. Requires explicit approval before executing each phase.
8. Pushes each completed phase and opens a pull request.
9. Blocks the next phase until the preceding pull request is merged.

Git pushes, pull-request creation, merge reconciliation, and crash recovery are deterministic host-owned
operations rather than model-owned shell actions.

Workflow interrupts, checkpoints, artifacts, source evidence, approvals, operations, and execution state
persist under `data/` and survive application restarts. On startup, MAFIA reconciles interrupted work and
existing GitHub pull requests before retrying side effects.

The selected primary model generates the specification, plan, and implementation. Its reviewer comes from
the configured model-pair mapping. The default mapping is reciprocal:

- `claude-opus-4.8` is reviewed by `gpt-5.6-sol`.
- `gpt-5.6-sol` is reviewed by `claude-opus-4.8`.

Both model families are accessed through the signed-in GitHub Copilot identity.

Set `MAFIA_MODEL_PAIRS` to a JSON object to adopt new model versions without changing the application. Each
key is a selectable primary or adjudicator model and its value is the independent reviewer, for example:

```dotenv
MAFIA_MODEL_PAIRS={"claude-sonnet-5":"gpt-5.7","gpt-5.7":"claude-sonnet-5"}
```

All referenced models must be available through the signed-in Copilot account. Existing runs retain the
model identifiers selected when they were created.

## Ad-hoc pull request review

Select **Review pull request**, enter a repository and pull request number or URL, and choose the
adjudicator model. MAFIA fetches the pull request's exact base and head commits and creates a read-only
analysis worktree at the head.

The configured pair then reviews the source and unified diff independently. Findings must cite lines
changed by the pull request; repository content and pull request text remain untrusted input. The selected
adjudicator inspects both reviews, records a disposition for every proposed finding, merges duplicates,
rejects weak claims, and persists one consolidated review.

The workflow pauses before any GitHub mutation. Choose **Post to pull request** to publish the consolidated
Markdown as an idempotent pull request comment, or **Finish without posting** to retain the local artifact
only. Model tools cannot post comments directly.

## Returning to the specification

After a specification exists, **Adjust specification** returns the run to that specification's decision
gate from planning, implementation, merge waiting, failure, cancellation, or completion. Active model work
is cancelled first. The active plan and phases that have not produced a pull request are invalidated, while
merged phases and phases with an open pull request remain immutable and continue to gate later work.

The reset creates a new durable AG-UI thread so stale artifact or phase decisions cannot resume. Refine the
restored specification with feedback, accept the new revision, and MAFIA will ground and adversarially
review a replacement plan against current source truth.
