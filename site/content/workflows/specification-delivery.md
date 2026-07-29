---
title: Specification delivery
description: Turn a requirement into a reviewed plan and dependency-ordered pull requests.
group: Workflows
order: 10
---

Create a run with an `owner/repository`, a GitHub issue number or URL or written requirement, and a primary model. Open the run and select **Start workflow**.

## Durable workflow

1. Ground and generate a structured specification.
2. Pause for the operator to accept the specification or refine it with feedback.
3. Ground an implementation plan in the current repository source.
4. Send the plan to the configured reviewer model.
5. Have the primary model adjudicate every finding and produce a revised, pull request-sized phased plan.
6. Pause for the operator to accept or refine the reviewed plan.
7. Require explicit approval before executing each phase.
8. Push each completed phase and open a pull request.
9. Block the next phase until the preceding pull request is merged.

Workflow interrupts, checkpoints, artifacts, source evidence, approvals, operations, and execution state persist under `data/`. On startup, mafia reconciles interrupted work and existing pull requests before retrying side effects.

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

The reset creates a new durable AG-UI thread so stale artifact or phase decisions cannot resume.
