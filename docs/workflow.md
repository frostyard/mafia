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
7. Resolves repository or host-owned deterministic validation before offering phase approval.
8. Runs the frozen validation commands, validates the implementation, and freezes its exact staged diff.
9. Sends that candidate to the competing model for one comprehensive implementation review.
10. Has the primary model adjudicate every finding and, when needed, perform one bounded remediation.
11. Has the competing model verify only the remediation's closure.
12. Pushes the accepted phase and opens a pull request.
13. Blocks the next phase until the preceding pull request is merged.

Git pushes, pull-request creation, merge reconciliation, and crash recovery are deterministic host-owned
operations rather than model-owned shell actions.

The implementation model may report useful targeted checks, but those checks are supplemental. The
repository or host-owned `.mafia.toml` commands form the mechanical gate and run again after remediation.

### Bounded implementation review

The implementation gate is deliberately finite: **review once, remediate once, verify once**. The competing
model reviews the exact staged candidate against the approved phase for requirement coverage, correctness,
security, compatibility, tests, operability, documentation, and pull-request scope. Every finding must cite
a changed line.

The primary model records an accepted, rejected, duplicate, or deferred disposition for every finding.
Accepted blocker and major findings receive one remediation pass; accepted minor findings are recorded but
do not trigger automatic edits. The competing model then performs closure-only verification of the accepted
serious findings and checks only for blocker or major regressions introduced by the remediation. Verification
cannot transition back to remediation.

If a serious finding remains unresolved, the phase fails before commit, push, or pull-request creation. The
operator can adjust the specification or explicitly retry the failed phase, which begins a new persisted
review cycle. Review, remediation, and verification budgets and candidate diff hashes survive process
restarts, so reconnecting cannot create an automatic review/fix loop.

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
adjudicator model. MAFIA fetches the pull request's exact base and head commits and creates an analysis
worktree at the head. Configured validation may write temporary output before MAFIA restores the exact head;
model review tools remain read-only.

Repository validation commands are resolved from the immutable base commit, not the pull-request head, so
the proposed change cannot introduce commands that MAFIA then executes.

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
