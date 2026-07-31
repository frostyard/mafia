---
title: Specification delivery
description: Turn a requirement into a reviewed plan and dependency-ordered pull requests.
group: Workflows
order: 10
---

Create a run with an `owner/repository`, a GitHub issue number or URL or written
requirement, and a primary model. Start it through the run page or
`POST /api/runs/{id}/start`. The API owns the launched background work, so the
browser can disconnect without cancelling it.

SQLite holds the run and its single current `pending_action`. Submit approval,
refinement, phase, or publication choices to
`POST /api/runs/{id}/decisions/{action_id}`. The run page does not guess which
control to show from state: it renders the persisted action and polls activity
every three seconds for changes.

## Durable workflow

1. Ground and generate a structured specification.
2. Pause for the operator to accept the specification or refine it with feedback.
3. Ground an implementation plan in the current repository source.
4. Send the plan to the configured reviewer model.
5. Have the primary model adjudicate every finding and produce a revised, pull request-sized phased plan.
6. Pause for the operator to accept or refine the reviewed plan.
7. Resolve deterministic project validation before offering phase approval.
8. Run the frozen validation commands, validate the implementation, and freeze its exact staged diff.
9. Send that candidate to the competing model for one comprehensive implementation review.
10. Have the primary model adjudicate every finding and perform one remediation when needed.
11. Have the competing model verify only the remediation's closure.
12. Push the accepted phase and open a pull request.
13. Block the next phase until the preceding pull request is merged.

<figure class="dk-screenshot">
  <img src="/images/app/specification-workflow.webp" alt="Specification delivery run awaiting reviewed plan approval with completed planning steps and source metrics" width="1680" height="1050" loading="lazy" />
  <figcaption>The run workspace combines durable stage state, artifact revisions, source metrics, and human-readable planning activity.</figcaption>
</figure>

Run state, pending actions, artifacts, source evidence, approvals, operations,
and execution state persist under `MAFIA_DATA_DIR`. If the API restarts during
working state, startup reconciliation marks the run failed rather than resuming
it. Use `POST /api/runs/{id}/retry` or the Retry control to explicitly begin the
next attempt from persisted state.

The implementation model's targeted checks are supplemental. Repository or host-owned `.mafia.toml` commands form the mechanical gate and run again after remediation.

## Bounded implementation review

The implementation gate is deliberately finite: **review once, remediate once, verify once**. The competing model reviews the exact staged candidate against the approved phase for requirement coverage, correctness, security, compatibility, tests, operability, documentation, and pull request scope. Every finding must cite a changed line.

The primary model records an accepted, rejected, duplicate, or deferred disposition for every finding. Accepted blocker and major findings receive one remediation pass. Accepted minor findings are recorded but do not trigger automatic edits.

After remediation, the competing model performs closure-only verification of the accepted serious findings and checks only for blocker or major regressions introduced by those edits. Verification cannot transition back to remediation.

If a serious finding remains unresolved, the phase fails before commit, push, or pull request creation. The operator can adjust the specification or explicitly retry the failed phase, which begins a new persisted review cycle. Review, remediation, and verification budgets and candidate diff hashes survive process restarts, preventing an automatic review/fix loop.

## Model pair

The selected primary model generates the specification, plan, and implementation. Its reviewer comes from `MAFIA_MODEL_PAIRS`.

The default mapping is reciprocal:

```dotenv
MAFIA_MODEL_PAIRS={"claude-opus-4.8":"gpt-5.6-sol","gpt-5.6-sol":"claude-opus-4.8"}
```

All referenced models must be available through the signed-in Copilot account. Existing runs retain the model identifiers selected when they were created.

## Return to the specification

After a specification exists, **Adjust specification** returns the run to that specification's decision gate from planning, implementation, merge waiting, failure, cancellation, or completion.

Active model work is cancelled first. The active plan and phases without a pull request are invalidated. Merged phases and phases with an open pull request remain immutable and continue to gate later work.

The reset replaces the current pending action so stale artifact or phase
decisions cannot be submitted.
