# Remove AG-UI Control Plane Design

## Goal

Remove AG-UI, CopilotKit, and Agent Framework workflow/checkpoint orchestration
from MAFIA. Replace them with an explicit REST control plane whose sole durable
truth is SQLite run state plus one persisted pending action per run.

Keep GitHub Copilot model invocation through `CopilotAgentService` and retain the
existing artifacts, operations, audit events, repository leases, state machine,
and activity polling.

## Motivation

The current implementation represents workflow state in three places:

1. the SQLite run and phase records;
2. Agent Framework checkpoints and pending `request_info` events; and
3. AG-UI snapshots and interrupts.

These representations can disagree. A run can be `ready_for_phase` while no
checkpoint request or AG-UI interrupt exists, leaving the frontend with a
"Restore decision controls" action that cannot restore anything. The application
does not otherwise benefit materially from a generic chat protocol: it has no
chat interface, its controls are fixed domain actions, and progress already comes
from `/activity` polling.

## Scope

Remove:

- the FastAPI `/ag-ui` endpoint;
- the Next.js `/api/copilotkit` runtime route;
- all CopilotKit providers, hooks, and agent-shell configuration;
- the AG-UI workflow wrapper and snapshot store;
- Agent Framework `WorkflowBuilder`, `WorkflowContext`, `request_info`, response
  handlers, and file checkpoint storage;
- the `agui_snapshots` table and checkpoint files;
- AG-UI and CopilotKit dependencies and environment variables.

Retain:

- `CopilotAgentService` and GitHub Copilot model integrations;
- domain schemas for specifications, plans, reviews, and decisions;
- SQLite run, phase, operation, artifact, evidence, and event persistence;
- deterministic state transitions and optimistic run versions;
- API-owned active-task cancellation and repository leases;
- startup reconciliation that marks interrupted work failed;
- the existing `/activity` polling model.

## Data Model

Add a `PendingAction` table with one row at most per run:

- `id`: immutable UUID used by the decision endpoint;
- `run_id`: unique foreign key to the run;
- `kind`: specification decision, plan decision, phase decision, pull-request
  review decision, or configuration required;
- `expected_run_version`: run version for stale-response rejection;
- `artifact_id`, `phase_id`, and `revision`: nullable subject identity fields;
- `payload`: display data such as prompt, title, objective, allowed actions, and
  project link context;
- `created_at` and `updated_at`.

The pending action and its corresponding run-state transition are written in one
transaction. Consuming a pending action validates its ID, kind, expected run
version, and subject identity before deleting it and performing or scheduling the
next transition. Duplicate and stale submissions return an explicit conflict and
cannot execute work twice.

`configuration_required` is a non-decision pending action. It records the exact
validation prerequisite failure and project identity. The UI renders a project
settings link and a "Check again" action rather than claiming a phase decision is
available.

## Workflow Services

Replace `RunWorkflowExecutor` with ordinary async domain services:

- `start_run(run_id)`: validate a startable state, register background work, and
  advance the workflow;
- `retry_run(run_id)`: perform retry preparation and launch the retry in one API
  operation;
- `submit_decision(run_id, pending_action_id, payload)`: validate and consume one
  explicit pending action;
- `advance_run(run_id)`: dispatch from persisted run state to the next model,
  deterministic operation, terminal result, or pending action.

Model and implementation work runs in API-owned background tasks rather than an
HTTP response stream. Browser navigation or disconnection cannot cancel work.
The existing active-task registry, cancellation endpoint, operation tracking,
repository lease, and heartbeat logic remain in use.

Replace Agent Framework context output with a small domain reporter that writes
operator-visible status messages to audit events or operation details. Replace
`request_info` calls with atomic pending-action creation. Decision handlers become
normal service functions called by REST endpoints.

If the API process exits during working states, startup reconciliation marks those
runs failed. Retry resumes from persisted artifacts according to existing state
dispatch logic. No durable job queue or automatic process-restart continuation is
introduced.

## API Contract

Add or change these endpoints:

- `POST /api/runs/{id}/start`: start or continue a startable run and return current
  activity;
- `POST /api/runs/{id}/retry`: prepare and launch a failed or stalled run, then
  return current activity;
- `POST /api/runs/{id}/decisions/{action_id}`: accept a discriminated payload for
  `accept`, `refine`, `start`, `cancel`, `post`, or `finish`;
- existing cancel, reset-to-specification, refresh, project, and activity routes
  remain domain REST operations.

`RunDetail` gains `pending_action`. The frontend does not infer decision controls
from run-state strings. Run state describes lifecycle position; `pending_action`
describes the one action currently available.

Start, retry, and decision endpoints reject invalid states and stale versions with
the existing structured API error envelope. Background launch registration occurs
before the endpoint returns, preventing rapid duplicate submissions.

## Frontend

Remove `CopilotProvider`, `CopilotChatConfigurationProvider`, `useAgent`,
`useAgentContext`, `useInterrupt`, and the CopilotKit runtime route.

The run page renders controls directly from `run.pending_action`:

- artifact actions show Accept and feedback-backed Refine;
- phase actions show Start phase;
- pull-request review actions show Post and Finish without posting;
- every decision supports Cancel where allowed;
- configuration-required actions show the backend message, a project settings
  link, and Check again.

Start and retry buttons call REST helpers directly. Reset and cancel retain their
existing REST paths. Control errors render locally and trigger `router.refresh()`
after successful commands.

Keep three-second activity polling. A run version, state, pending-action, artifact,
or phase change triggers a server-component refresh. Polling stops for terminal
states. No SSE or WebSocket transport is added.

## Host Validation Correction

Preserve the in-progress correction to `source_validation_status`: use Git object
checks to distinguish an invalid source commit from an absent
`<sha>:.mafia.toml`, rather than parsing Git's stderr wording. When the repository
file is absent, configured host validation is used and phase approval can be
created. The temporary CopilotKit reconnect fallback is removed with the old
frontend control plane.

## Destructive Data Reset

This is an intentionally breaking migration. Preserve no existing runtime data.
Before the first startup of the replacement:

1. stop API and web processes;
2. delete the configured `MAFIA_DATA_DIR` in full, including the database,
   projects, repository caches, worktrees, checkpoints, and configuration;
3. recreate the directory with deployment ownership and permissions;
4. run Alembic migrations against a new database;
5. restart API and web services.

Do not perform this deletion silently during ordinary startup. Add an explicit
reset command and deployment documentation. Execute the reset for the current
development instance only after implementation and verification are complete.

The new Alembic head creates `pending_actions` and omits or drops
`agui_snapshots`. No compatibility or pending-action backfill code is required.

## Dependency And Configuration Removal

Remove Python dependencies used only by AG-UI workflow transport. Retain the
GitHub Copilot Agent Framework integration required by `CopilotAgentService`.

Remove web dependencies under `@ag-ui/*` and `@copilotkit/*` when no remaining
runtime or test import requires them. Regenerate lockfiles.

Remove `AGENT_URL`, `NEXT_PUBLIC_COPILOTKIT_RUNTIME_URL`, CopilotKit telemetry
configuration, Caddy's CopilotKit matcher, and documentation describing AG-UI
transport. Caddy routes ordinary `/api/*` requests to FastAPI or the existing
Next.js API proxies according to the established deployment boundary.

## Testing

Use test-driven implementation for every control-plane behavior:

- pending-action creation is atomic with each decision state;
- start and retry register exactly one background task;
- browser/request cancellation does not cancel detached workflow work;
- every decision kind validates ID, kind, version, subject, and payload;
- duplicate and stale decisions are harmless conflicts;
- configuration-required actions expose accurate project guidance and become
  phase decisions after configuration is valid;
- cancel and reset interact correctly with pending actions and active work;
- process restart marks working states failed and permits explicit retry;
- run detail exposes pending actions and frontend controls submit exact payloads;
- polling refreshes structural changes and stops at terminal states;
- host validation falls back without parsing Git stderr;
- dependency, route, and source scans find no AG-UI, CopilotKit, checkpoint,
  snapshot, `useAgent`, `useInterrupt`, or `request_info` control surfaces.

Final verification includes API lint, type checking, and all tests; web lint,
type checking, tests, and production build; site tests; shell syntax checks; a
fresh-data migration; and a live smoke test from run creation through pending
decision submission.

## Success Criteria

- SQLite run state plus `PendingAction` is the only workflow control-plane truth.
- No AG-UI or CopilotKit runtime, dependency, route, snapshot, or UI hook remains.
- No Agent Framework workflow/checkpoint/request-info orchestration remains.
- Browser disconnects do not stop model or implementation work.
- Every visible decision corresponds to one persisted pending action.
- A run cannot claim a restorable decision when no actionable record exists.
- Fresh-data setup, full automated checks, and the end-to-end smoke workflow pass.
