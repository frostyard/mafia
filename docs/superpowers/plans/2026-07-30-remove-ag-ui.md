# Remove AG-UI Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AG-UI, CopilotKit, and Agent Framework workflow orchestration with a SQLite-backed REST control plane while retaining GitHub Copilot model execution.

**Architecture:** A unique `PendingAction` row is the only durable representation of user input required by a run. FastAPI commands atomically consume or create that row with versioned run transitions, then launch API-owned background tasks whose lifetime is independent of the browser request. The Next.js UI renders pending actions from `RunDetail` and continues to use three-second activity polling.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, Alembic, Pydantic, pytest, Next.js 16, React 19, TypeScript, Vitest, SQLite, Bash

## Global Constraints

- Preserve `CopilotAgentService` and `agent-framework-github-copilot==1.0.0`.
- Remove AG-UI, CopilotKit, `WorkflowBuilder`, checkpoints, snapshots, and `request_info`.
- SQLite run state plus one `PendingAction` per run is the sole workflow control-plane truth.
- Keep three-second `/activity` polling; do not add SSE or WebSockets.
- Browser disconnection must not cancel API-owned background work.
- Process restart during working states marks the run failed and requires explicit Retry.
- Preserve operations, audit events, optimistic run versions, repository leases, cancellation, reset, and merge monitoring.
- Discard all existing runtime data during rollout; do not add compatibility or backfill behavior.
- Never silently delete `MAFIA_DATA_DIR` during ordinary startup.
- Preserve the current `source_validation_status()` Git object-check correction.
- Delete the temporary `workflow-control.ts` reconnect fallback with the old transport.
- Leave the unrelated generated `apps/web/next-env.d.ts` worktree change untouched and uncommitted.

---

### Task 1: Pending Action Persistence And Contracts

**Files:**
- Modify: `apps/api/src/mafia/domain/enums.py`
- Modify: `apps/api/src/mafia/db/models.py`
- Modify: `apps/api/src/mafia/domain/schemas.py`
- Modify: `apps/api/src/mafia/services/runs.py`
- Create: `apps/api/migrations/versions/e4c2a81f0d31_replace_agui_with_pending_actions.py`
- Create: `apps/api/tests/test_pending_actions.py`
- Modify: `apps/api/tests/test_schemas.py`

**Interfaces:**
- Produces: `PendingActionKind`, `PendingAction`, `PendingActionRead`, `DecisionSubmission`.
- Produces: `transition_with_pending_action(session, run_id, target, expected_version, event_type, pending, payload=None) -> Run`.
- Produces: `PendingActionSpec(kind, artifact_id=None, phase_id=None, revision=None, payload={})`.
- Consumes: existing `transition_run`, `Run.version`, `RunDetail`, and SQLAlchemy session patterns.

- [ ] **Step 1: Write failing model and schema tests**

Add tests that create a run with a pending action, reject a second action for the same run, serialize it through `RunDetail`, require feedback for `refine`, and reject feedback on non-refine actions:

```python
async def test_run_has_only_one_pending_action(session_factory):
    async with session_factory() as session:
        run = await add_run(session, state=RunState.AWAITING_SPEC_DECISION)
        session.add(PendingAction(run_id=run.id, kind=PendingActionKind.SPECIFICATION, expected_run_version=run.version, payload={}))
        await session.commit()
        session.add(PendingAction(run_id=run.id, kind=PendingActionKind.PLAN, expected_run_version=run.version, payload={}))
        with pytest.raises(IntegrityError):
            await session.commit()

def test_refine_requires_non_blank_feedback():
    with pytest.raises(ValidationError):
        DecisionSubmission(action="refine", feedback="  ")
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `uv run --frozen pytest -q apps/api/tests/test_pending_actions.py apps/api/tests/test_schemas.py`

Expected: FAIL because `PendingAction`, `PendingActionKind`, and `DecisionSubmission` do not exist.

- [ ] **Step 3: Add the enum, ORM model, and API schemas**

Use these exact public shapes:

```python
class PendingActionKind(StrEnum):
    SPECIFICATION = "specification"
    PLAN = "plan"
    PHASE = "phase"
    PULL_REQUEST_REVIEW = "pull_request_review"
    CONFIGURATION_REQUIRED = "configuration_required"

class PendingAction(Base, TimestampMixin):
    __tablename__ = "pending_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True, index=True)
    kind: Mapped[PendingActionKind] = mapped_column(Enum(PendingActionKind, values_callable=enum_values, native_enum=False, length=40))
    expected_run_version: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"))
    phase_id: Mapped[str | None] = mapped_column(ForeignKey("phases.id", ondelete="CASCADE"))
    revision: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run: Mapped[Run] = relationship(back_populates="pending_action")
```

Add `Run.pending_action` with `uselist=False, cascade="all, delete-orphan"`. Remove `Run.thread_id` and `AGUISnapshot`. Add `PendingActionRead` to `RunDetail`, remove `thread_id` from `RunRead`, and define:

```python
class DecisionSubmission(BaseModel):
    action: Literal["accept", "refine", "start", "cancel", "post", "finish", "check_again"]
    feedback: str | None = Field(default=None, max_length=100_000)

    @model_validator(mode="after")
    def valid_feedback(self) -> "DecisionSubmission":
        if self.action == "refine" and not (self.feedback and self.feedback.strip()):
            raise ValueError("Refinement feedback is required")
        if self.action != "refine" and self.feedback is not None:
            raise ValueError("Feedback is only valid for refinement")
        return self
```

- [ ] **Step 4: Add an atomic transition helper**

Add a frozen `PendingActionSpec` dataclass and implement `transition_with_pending_action` in `services/runs.py`. It must perform the optimistic `UPDATE runs ... WHERE version = expected_version`, delete any old pending row, insert the new row with `expected_run_version=expected_version + 1`, add the audit event, and commit once. Refactor `transition_run` to call the same private no-commit transition primitive so both paths share state-machine and version checks.

```python
@dataclass(frozen=True)
class PendingActionSpec:
    kind: PendingActionKind
    artifact_id: str | None = None
    phase_id: str | None = None
    revision: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 5: Add the destructive-schema migration**

Set `down_revision = "b8a1c7d4e2f0"`. The migration must drop `agui_snapshots`, create `pending_actions` with its unique run constraint and foreign keys, and drop `runs.thread_id`. Its downgrade recreates `thread_id` and `agui_snapshots` only as empty schema; it does not reconstruct data.

- [ ] **Step 6: Run focused tests and migration smoke tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_pending_actions.py apps/api/tests/test_schemas.py`

Run: `tmp=$(mktemp -d) && MAFIA_DATA_DIR="$tmp" uv run --frozen alembic -c alembic.ini upgrade head && rm -rf "$tmp"`

Expected: all tests PASS and Alembic exits 0.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/mafia/domain/enums.py apps/api/src/mafia/db/models.py apps/api/src/mafia/domain/schemas.py apps/api/src/mafia/services/runs.py apps/api/migrations/versions/e4c2a81f0d31_replace_agui_with_pending_actions.py apps/api/tests/test_pending_actions.py apps/api/tests/test_schemas.py
git commit -m "feat: add durable pending run actions"
```

### Task 2: Detached Background Work And Failure Handling

**Files:**
- Modify: `apps/api/src/mafia/services/operations.py`
- Create: `apps/api/src/mafia/services/run_control.py`
- Create: `apps/api/tests/test_run_control.py`

**Interfaces:**
- Consumes: `active_run_work`, `has_active_work`, `transition_run`, `ALLOWED_TRANSITIONS`.
- Produces: `launch_background_work(run_id: str, worker: Callable[[], Awaitable[None]]) -> None`.
- Produces: `record_run_failure(run_id: str, stage: str, error: BaseException) -> None`.
- Produces: `record_run_status(run_id: str, event_type: str, message: str, payload: dict[str, object] | None = None) -> None`.
- Produces: `start_run(run_id: str) -> RunActivity`, `retry_run(run_id: str) -> RunActivity`, and `advance_run(run_id: str, feedback: str | None = None, phase_id: str | None = None) -> None`.

- [ ] **Step 1: Write race, disconnect, and failure tests**

Test that a second launch is rejected before the first task receives an event-loop turn, that cancelling the request task does not cancel the launched worker, and that worker exceptions transition the run to `FAILED` with a bounded message:

```python
async def test_launch_registers_before_worker_runs():
    release = asyncio.Event()
    launch_background_work("run-1", lambda: release.wait())
    with pytest.raises(RunControlError, match="already active"):
        launch_background_work("run-1", lambda: release.wait())
    release.set()

async def test_worker_failure_marks_run_failed(run_control_session_factory, monkeypatch):
    monkeypatch.setattr(run_control, "SessionFactory", run_control_session_factory)
    await run_control.record_run_failure("run-1", "planning", RuntimeError("boom"))
    async with run_control_session_factory() as session:
        run = await get_run(session, "run-1")
        assert run.state == RunState.FAILED
        assert run.failure_code == "planning_failed"
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_run_control.py`

Expected: FAIL because the detached launcher and control service do not exist.

- [ ] **Step 3: Implement immediate task registration**

Add this public launcher around the existing task registry:

```python
def launch_background_work(run_id: str, worker: Callable[[], Awaitable[None]]) -> None:
    if has_active_work(run_id):
        raise ActiveWorkError(f"Run {run_id} already has active work")
    task = asyncio.create_task(worker(), name=f"run-{run_id}")
    _register_active_task(run_id, task)
    task.add_done_callback(lambda completed: _finish_background_task(run_id, completed))
```

`_finish_background_task` must unregister, ignore cancellation, and log an exception already persisted by the worker. Keep reference counting because `tracked_operation` may register the same task.

- [ ] **Step 4: Implement the control service shell**

Create `run_control.py` with startable-state dispatch and a guarded worker:

```python
async def _run_guarded(run_id: str, stage: str, work: Callable[[], Awaitable[None]]) -> None:
    try:
        await work()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await record_run_failure(run_id, stage, error)

async def start_run(run_id: str) -> RunActivity:
    async with SessionFactory() as session:
        run = await get_run(session, run_id)
        if run.state != RunState.INTAKE:
            raise RunControlError("Only an intake run can be started")
    launch_background_work(run_id, lambda: _run_guarded(run_id, "workflow", lambda: advance_run(run_id)))
    return await get_run_activity(run_id)
```

Implement retry validation now, but leave state-specific `advance_run` branches calling explicit private functions added in Tasks 3-5.

Add `record_run_status` as the replacement for operator-visible framework output. It inserts one `AuditEvent` whose payload is `{"message": message, **(payload or {})}` and commits. Use it only for useful status that is not already represented by a transition or operation detail.

- [ ] **Step 5: Run focused tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_run_control.py apps/api/tests/test_operations.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/mafia/services/operations.py apps/api/src/mafia/services/run_control.py apps/api/tests/test_run_control.py apps/api/tests/test_operations.py
git commit -m "feat: launch run work independently of requests"
```

### Task 3: Specification And Plan Workflow Services

**Files:**
- Modify: `apps/api/src/mafia/services/run_control.py`
- Replace: `apps/api/tests/test_workflow_decisions.py`
- Modify: `apps/api/tests/test_artifacts.py`

**Interfaces:**
- Consumes: `ArtifactGenerator`, `PendingActionSpec`, `transition_with_pending_action`, `DecisionSubmission`.
- Produces: specification and plan generation branches in `advance_run`.
- Produces: `submit_decision(run_id: str, pending_action_id: str, payload: DecisionSubmission) -> RunActivity` for artifact actions.

- [ ] **Step 1: Replace AG-UI artifact tests with persisted-action tests**

Port the existing behavioral cases without `RecordingContext` or `WorkflowContext`. Cover generation, restored decision state, accept, refine, cancel, stale action ID, stale run version, wrong artifact, and the open-PR phase preservation case. Assert rows and audit events, not streamed output.

```python
action = await session.scalar(select(PendingAction).where(PendingAction.run_id == run.id))
assert action.kind == PendingActionKind.SPECIFICATION
assert action.artifact_id == artifact.id
assert action.expected_run_version == run.version
```

- [ ] **Step 2: Run artifact workflow tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_decisions.py -k 'specification or plan or artifact'`

Expected: FAIL because `run_control` does not yet generate or consume artifact actions.

- [ ] **Step 3: Port specification generation**

Move `_generate_specification_inner` from `workflows/run_workflow.py` into `run_control.py`, remove `ctx`, and replace the final transition plus `add_event/request_info` calls with one `transition_with_pending_action` call:

```python
await transition_with_pending_action(
    session,
    run.id,
    RunState.AWAITING_SPEC_DECISION,
    expected_version=run.version,
    event_type="specification.generated",
    payload={"artifact_id": artifact.id, "revision": artifact.revision},
    pending=PendingActionSpec(
        kind=PendingActionKind.SPECIFICATION,
        artifact_id=artifact.id,
        revision=artifact.revision,
        payload={"prompt": "Accept this specification or refine it with feedback."},
    ),
)
```

- [ ] **Step 4: Port plan generation and acceptance**

Move the current grounding, draft, adversarial review, adjudication, persistence, immutable-phase, and phase creation logic unchanged except for context output. End plan generation with a `PLAN` pending action. On acceptance, atomically consume the plan action and transition to `WAITING_FOR_MERGE`, `COMPLETED`, or Task 4's phase-action helper.

- [ ] **Step 5: Implement strict artifact action consumption**

Before writing `Decision`, validate all of these in one session: action ID, run ID, pending kind, expected run version, artifact ID, revision, active revision, run state, and allowed action set. Raise `RunControlError` on any mismatch. Delete the pending row in the same commit that records the decision and changes state. Launch generation only after commit.

- [ ] **Step 6: Run artifact workflow tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_decisions.py apps/api/tests/test_artifacts.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/mafia/services/run_control.py apps/api/tests/test_workflow_decisions.py apps/api/tests/test_artifacts.py
git commit -m "feat: move specification and plan flow to sqlite"
```

### Task 4: Phase Actions, Validation, And Execution

**Files:**
- Modify: `apps/api/src/mafia/services/run_control.py`
- Modify: `apps/api/src/mafia/services/execution.py`
- Modify: `apps/api/src/mafia/services/lifecycle.py`
- Modify: `apps/api/src/mafia/services/project_config.py`
- Modify: `apps/api/tests/test_workflow_project_validation.py`
- Modify: `apps/api/tests/test_execution.py`
- Modify: `apps/api/tests/test_project_config.py`
- Modify: `apps/api/tests/test_operations.py`

**Interfaces:**
- Produces: `create_phase_pending_action(run_id: str, phase_id: str) -> None`.
- Changes: `execute_phase(run_id: str, phase_id: str) -> None`, with no framework context.
- Consumes: approved Git object-check correction in `source_validation_status()`.

- [ ] **Step 1: Write phase and configuration action tests**

Cover repository validation, host validation fallback, malformed configuration, `configuration_required`, Check again, Start, Cancel, stale phase identity, source drift, merge reconciliation, and reset cleanup. The unavailable-validation assertion must be durable:

```python
assert action.kind == PendingActionKind.CONFIGURATION_REQUIRED
assert action.phase_id == phase.id
assert action.payload == {
    "message": "Phase 1 cannot start until deterministic validation is configured for octo/repo.",
    "project_id": repository.id,
    "project_href": f"/projects/{repository.id}",
}
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_project_validation.py apps/api/tests/test_execution.py apps/api/tests/test_project_config.py`

Expected: pending-action assertions FAIL while the existing Git object-check regression remains PASS.

- [ ] **Step 3: Implement phase-action creation**

`create_phase_pending_action` loads the run, phase, and repository identity; calls `source_validation_status`; and creates either `CONFIGURATION_REQUIRED` or `PHASE`. Both paths must use the same transaction as the transition to `READY_FOR_PHASE` when a transition is required. `check_again` consumes only a matching configuration action and recreates the correct next action.

- [ ] **Step 4: Remove Agent Framework from execution**

Change both execution signatures to omit `ctx`. Replace source-drift output with an audit payload on `source.drift_detected`. Remove the terminal `WorkflowEvent` and output because phase result, operation result, PR URL, and transitions are already persisted.

```python
async def execute_phase(run_id: str, phase_id: str) -> None:
    async with active_run_work(run_id):
        await _execute_phase(run_id, phase_id)
```

- [ ] **Step 5: Wire Start and merge reconciliation**

The Start action transaction records `DecisionType.START_PHASE`, deletes the pending action, and leaves `execute_phase` to perform its guarded readiness transition. After merge, `reconcile_run` must call the same phase-action creation path for the next ready phase; material drift launches re-grounding, and completion creates no action.

- [ ] **Step 6: Update reset and cancellation invariants**

Reset deletes the current pending action and creates a specification pending action in the same versioned transition. Remove thread rotation and AG-UI snapshot deletion. Cancellation deletes pending actions when cancellation is an allowed explicit decision; activity-rail cancellation remains restricted to active work.

- [ ] **Step 7: Run phase and operation tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_project_validation.py apps/api/tests/test_execution.py apps/api/tests/test_project_config.py apps/api/tests/test_operations.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/mafia/services/run_control.py apps/api/src/mafia/services/execution.py apps/api/src/mafia/services/lifecycle.py apps/api/src/mafia/services/project_config.py apps/api/tests/test_workflow_project_validation.py apps/api/tests/test_execution.py apps/api/tests/test_project_config.py apps/api/tests/test_operations.py
git commit -m "feat: persist phase approval and configuration gates"
```

### Task 5: Pull Request Review Actions And Retry Dispatch

**Files:**
- Modify: `apps/api/src/mafia/services/run_control.py`
- Modify: `apps/api/src/mafia/services/lifecycle.py`
- Modify: `apps/api/tests/test_workflow_decisions.py`
- Modify: `apps/api/tests/test_pr_reviews.py`
- Modify: `apps/api/tests/test_operations.py`

**Interfaces:**
- Consumes: `PullRequestReviewService`, `post_pull_request_comment`, `PendingActionKind.PULL_REQUEST_REVIEW`.
- Completes: `advance_run`, `submit_decision`, and `retry_run` state dispatch.

- [ ] **Step 1: Write PR-review and retry tests**

Cover initial grounding, two-model review, consolidation, pending publication action, Post, Finish, Cancel, stale revision, post failure, retry after every persisted working stage, failed phase PR recovery, and exact-once launch.

```python
await submit_decision(run.id, action.id, DecisionSubmission(action="finish"))
async with session_factory() as session:
    completed = await get_run(session, run.id)
    assert completed.state == RunState.COMPLETED
    assert completed.pending_action is None
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_decisions.py -k pull_request apps/api/tests/test_pr_reviews.py apps/api/tests/test_operations.py -k retry`

Expected: FAIL because PR review and complete retry dispatch still depend on the framework executor.

- [ ] **Step 3: Port PR-review generation and decisions**

Move the existing snapshot, independent reviews, consolidation, artifact persistence, and post-comment operation into `run_control.py`. Replace `request_info` with a `PULL_REQUEST_REVIEW` action carrying prompt, artifact ID, revision, and pull-request number. Validate those fields before Post or Finish.

- [ ] **Step 4: Complete retry state dispatch**

After stalled-work shutdown or failed-state validation, launch `advance_run` directly. Preserve the current recovery order: recover a failed phase PR when possible; otherwise retry that phase; resume planning when a specification was accepted; restore an artifact/publication action when durable artifacts identify one; otherwise restart the workflow type's initial generation. Never reconstruct state from a checkpoint.

- [ ] **Step 5: Verify startup recovery semantics**

Keep `recover_interrupted_runs()` marking working runs and running operations failed. Update messages from “Start the workflow” to “Retry the run.” Assert that startup does not create a task or pending action for interrupted work.

- [ ] **Step 6: Run workflow and recovery tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_decisions.py apps/api/tests/test_pr_reviews.py apps/api/tests/test_operations.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/mafia/services/run_control.py apps/api/src/mafia/services/lifecycle.py apps/api/tests/test_workflow_decisions.py apps/api/tests/test_pr_reviews.py apps/api/tests/test_operations.py
git commit -m "feat: replace review workflow and checkpoint retries"
```

### Task 6: REST Control Plane

**Files:**
- Modify: `apps/api/src/mafia/api/routes.py`
- Modify: `apps/api/src/mafia/services/activity.py`
- Modify: `apps/api/src/mafia/domain/schemas.py`
- Create: `apps/api/tests/test_run_control_api.py`
- Modify: `apps/api/tests/test_auth.py`

**Interfaces:**
- Consumes: `start_run`, `retry_run`, `submit_decision`, and existing API error envelope.
- Produces: `POST /api/runs/{id}/start`, `POST /api/runs/{id}/retry`, `POST /api/runs/{id}/decisions/{action_id}`.
- Changes: `RunActivity.pending_action: PendingActionRead | None`.

- [ ] **Step 1: Write API contract tests**

Use the existing FastAPI test-client/auth setup. Assert operator authorization, 404 for missing runs, 409 for control conflicts, 422 for malformed payloads, and successful `RunActivity` responses for all three endpoints.

```python
response = client.post(
    f"/api/runs/{run_id}/decisions/{action_id}",
    json={"action": "accept"},
    headers=operator_headers,
)
assert response.status_code == 200
assert response.json()["pending_action"] is None
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_run_control_api.py apps/api/tests/test_auth.py`

Expected: FAIL with missing routes and response fields.

- [ ] **Step 3: Add route handlers and conflict mapping**

Use the established `RunNotFoundError -> 404` and `(ConcurrentUpdateError, RunControlError) -> 409` mappings. Replace `prepare_retry` with `retry_run`. Every command endpoint requires `Operator`; activity and run detail retain their current read authorization.

- [ ] **Step 4: Expose pending actions in reads and polling**

Eager-load `Run.pending_action` in `get_run`, add it to `RunDetail`, and include it in `RunActivity`. Derive decision status from the presence and kind of the pending action, not solely from run state; `configuration_required` uses decision mode with its persisted message.

- [ ] **Step 5: Run API and backend tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_run_control_api.py apps/api/tests/test_auth.py apps/api/tests/test_pending_actions.py apps/api/tests/test_operations.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/mafia/api/routes.py apps/api/src/mafia/services/activity.py apps/api/src/mafia/domain/schemas.py apps/api/tests/test_run_control_api.py apps/api/tests/test_auth.py
git commit -m "feat: expose workflow controls over rest"
```

### Task 7: Frontend REST Types, Helpers, And Polling

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/lib/activity-refresh.ts`
- Modify: `apps/web/src/lib/activity-refresh.test.ts`
- Create: `apps/web/src/lib/api.test.ts`

**Interfaces:**
- Produces: TypeScript `PendingAction`, `DecisionPayload`, `startRun`, `retryRun`, and `submitDecision`.
- Consumes: backend JSON contract from Task 6.

- [ ] **Step 1: Write helper and refresh tests**

Assert exact methods, encoded paths, JSON bodies, error preservation, refresh on pending-action identity/kind/subject changes, and no refresh for unchanged polling data.

```ts
expect(shouldRefreshRunPage(
  activity({ pending_action: null }),
  activity({ pending_action: pendingAction({ id: "action-1" }) }),
)).toBe(true);
```

- [ ] **Step 2: Run focused web tests and verify failure**

Run: `npm test --prefix apps/web -- --run src/lib/api.test.ts src/lib/activity-refresh.test.ts`

Expected: FAIL because REST action helpers and pending-action fields are absent.

- [ ] **Step 3: Add exact TypeScript contracts**

Remove `thread_id`. Add:

```ts
export type PendingActionKind = "specification" | "plan" | "phase" | "pull_request_review" | "configuration_required";
export interface PendingAction {
  id: string;
  kind: PendingActionKind;
  expected_run_version: number;
  artifact_id: string | null;
  phase_id: string | null;
  revision: number | null;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
export type DecisionPayload =
  | { action: "accept" | "start" | "cancel" | "post" | "finish" | "check_again" }
  | { action: "refine"; feedback: string };
```

Add `pending_action` to both `RunDetail` and `RunActivity`.

- [ ] **Step 4: Add REST helpers**

```ts
export function startRun(id: string): Promise<RunActivity> {
  return request<RunActivity>(`/api/runs/${encodeURIComponent(id)}/start`, {
    method: "POST",
  });
}

export function retryRun(id: string): Promise<RunActivity> {
  return request<RunActivity>(`/api/runs/${encodeURIComponent(id)}/retry`, {
    method: "POST",
  });
}

export function submitDecision(
  runId: string,
  actionId: string,
  payload: DecisionPayload,
): Promise<RunActivity> {
  return request<RunActivity>(
    `/api/runs/${encodeURIComponent(runId)}/decisions/${encodeURIComponent(actionId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}
```

Implement each using the existing `request` function, `encodeURIComponent`, `Content-Type: application/json` for decisions, and no request body for start/retry. Remove `prepareRunRetry`.

- [ ] **Step 5: Expand the structural polling signature**

Include `state`, `version`, pending action ID/kind/artifact/phase/revision, and preserve completed-artifact operation detection. Refresh whenever the signature changes, including during working states.

- [ ] **Step 6: Run focused tests**

Run: `npm test --prefix apps/web -- --run src/lib/api.test.ts src/lib/activity-refresh.test.ts`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/lib/api.ts apps/web/src/lib/activity-refresh.ts apps/web/src/lib/activity-refresh.test.ts apps/web/src/lib/api.test.ts
git commit -m "feat: add rest workflow client contracts"
```

### Task 8: Direct Run Controls And Provider Removal

**Files:**
- Replace: `apps/web/src/components/workflow-panel.tsx`
- Replace: `apps/web/src/components/workflow-panel.test.tsx`
- Modify: `apps/web/src/components/visibility-rail.tsx`
- Modify: `apps/web/src/components/visibility-rail.test.tsx`
- Modify: `apps/web/src/app/runs/[id]/page.tsx`
- Modify: `apps/web/src/app/layout.tsx`
- Delete: `apps/web/src/components/copilot-provider.tsx`
- Delete: `apps/web/src/components/run-agent-shell.tsx`
- Delete: `apps/web/src/app/api/copilotkit/route.ts`
- Delete: `apps/web/src/lib/workflow-control.ts`
- Delete: `apps/web/src/lib/workflow-control.test.ts`

**Interfaces:**
- Consumes: `RunDetail.pending_action`, `startRun`, `retryRun`, `submitDecision`, reset/cancel helpers.
- Produces: direct controls with no agent connection or interrupt state.

- [ ] **Step 1: Replace component tests first**

Test Start, Retry, specification/plan Accept and Refine, phase Start, PR review Post and Finish, Cancel, configuration guidance and Check again, disabled submission, backend error text, refresh after success, and reset confirmation. Assert that “Restore decision controls,” durable thread text, and connection warnings never render.

```tsx
render(<WorkflowPanel run={run({ pending_action: phaseAction })} />);
fireEvent.click(screen.getByRole("button", { name: "Start phase" }));
await waitFor(() => expect(submitDecision).toHaveBeenCalledWith("run-1", "action-1", { action: "start" }));
```

- [ ] **Step 2: Run component tests and verify failure**

Run: `npm test --prefix apps/web -- --run src/components/workflow-panel.test.tsx src/components/visibility-rail.test.tsx`

Expected: FAIL because the components still use CopilotKit.

- [ ] **Step 3: Replace `WorkflowPanel`**

Accept one `run: RunDetail` prop. Render start only for `intake`, retry in the visibility rail only when `can_retry`, and a decision card only when `run.pending_action` exists. Read display strings defensively from `payload`; fall back to stable copy. For configuration actions, link to `/projects/${project_id}` and submit `{action: "check_again"}`.

- [ ] **Step 4: Remove agent usage from the activity rail**

Delete `useAgent`; call `retryRun(runId)`, update local activity, and refresh. Keep the existing timer at `3_000`, terminal stop behavior, cancellation, operation display, and error handling.

- [ ] **Step 5: Remove providers and page plumbing**

Remove `CopilotProvider` from root layout. Remove `RunAgentShell`, `threadId`, and agent configuration from the run page. Pass the complete `run` to `WorkflowPanel`. Delete the CopilotKit route, provider, shell, workflow helper, and helper tests.

- [ ] **Step 6: Run web tests and static checks**

Run: `npm test --prefix apps/web -- --run src/components/workflow-panel.test.tsx src/components/visibility-rail.test.tsx`

Run: `npm run lint --prefix apps/web -- --quiet && npm run typecheck --prefix apps/web`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src
git commit -m "feat: render workflow controls from pending actions"
```

Do not stage `apps/web/next-env.d.ts`.

### Task 9: Remove Backend Transport And Framework Workflow

**Files:**
- Modify: `apps/api/src/mafia/main.py`
- Modify: `apps/api/src/mafia/config.py`
- Modify: `apps/api/src/mafia/domain/artifacts.py`
- Delete: `apps/api/src/mafia/agui/workflow.py`
- Delete: `apps/api/src/mafia/agui/snapshots.py`
- Delete: `apps/api/src/mafia/workflows/run_workflow.py`
- Delete: `apps/api/tests/test_agui_restart.py`
- Delete: `apps/api/tests/test_agui_snapshots.py`
- Modify: `apps/api/tests/test_frontend_contracts.py`
- Modify: `apps/api/tests/test_config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`

**Interfaces:**
- Retains: `CopilotAgentService` and all model-generating services.
- Removes: `/ag-ui`, checkpoint storage, snapshot storage, request/response framework schemas, and transport dependencies.

- [ ] **Step 1: Add absence contract tests**

Assert `/ag-ui` is 404, settings have no checkpoint directory, frontend source has no CopilotKit route, and source imports do not include `agent_framework.ag_ui`, workflow framework classes, or request-info APIs.

- [ ] **Step 2: Run absence tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py apps/api/tests/test_config.py`

Expected: FAIL because the old endpoint and configuration remain.

- [ ] **Step 3: Remove backend integration and old schemas**

Delete AG-UI initialization and endpoint registration from `main.py` while retaining lifespan recovery and merge monitoring. Remove `checkpoints_dir`. Delete `ArtifactDecisionRequest`, `PhaseDecisionRequest`, and `PullRequestReviewDecisionRequest`; retain reusable decision payload models only if imported by the new REST service.

- [ ] **Step 4: Delete obsolete modules and tests**

Delete the AG-UI package implementations, framework workflow executor, checkpoint/restart tests, and snapshot tests. Remove empty package directories only when no imports remain.

- [ ] **Step 5: Remove dependencies and regenerate locks**

Remove `agent-framework-ag-ui==1.0.0` but retain `agent-framework-github-copilot==1.0.0`, then run `uv lock`. Remove all `@ag-ui/*` and `@copilotkit/*` packages, then run `npm install --prefix apps/web --package-lock-only`.

- [ ] **Step 6: Run backend and web checks**

Run: `uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py apps/api/tests/test_config.py`

Run: `npm run typecheck --prefix apps/web`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src apps/api/tests pyproject.toml uv.lock apps/web/package.json apps/web/package-lock.json
git commit -m "refactor: remove ag-ui workflow transport"
```

### Task 10: Explicit Data Reset And Deployment Configuration

**Files:**
- Create: `packaging/bin/reset-data`
- Create: `apps/api/tests/test_reset_script.py`
- Modify: `scripts/build-release.sh`
- Modify: `package.json`
- Modify: `.env.example`
- Modify: `contrib/Caddyfile`
- Modify: `apps/api/tests/test_deployment_assets.py`

**Interfaces:**
- Produces: `bin/reset-data --confirm-destructive-reset`.
- Consumes: `MAFIA_DATA_DIR`; never infers or defaults a destructive target.

- [ ] **Step 1: Write reset safety tests**

Test missing confirmation, missing `MAFIA_DATA_DIR`, root target, successful deletion/recreation of a temporary data directory, executable release inclusion, and absence of AG-UI/CopilotKit Caddy/env settings.

- [ ] **Step 2: Run deployment tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_reset_script.py apps/api/tests/test_deployment_assets.py`

Expected: FAIL because the explicit reset command does not exist and old configuration remains.

- [ ] **Step 3: Add the guarded reset command**

Use this behavior:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" != "--confirm-destructive-reset" ]]; then
  printf 'Refusing to delete runtime data without --confirm-destructive-reset.\n' >&2
  exit 2
fi
: "${MAFIA_DATA_DIR:?Set MAFIA_DATA_DIR to the runtime data directory}"
case "$MAFIA_DATA_DIR" in
  /|.|.. ) printf 'Refusing unsafe MAFIA_DATA_DIR: %s\n' "$MAFIA_DATA_DIR" >&2; exit 2 ;;
esac
rm -rf -- "$MAFIA_DATA_DIR"
install -d -m 0750 -- "$MAFIA_DATA_DIR"
```

- [ ] **Step 4: Remove transport configuration**

Remove `AGENT_URL`, `NEXT_PUBLIC_COPILOTKIT_RUNTIME_URL`, and CopilotKit telemetry variables. Remove Caddy's `/ag-ui` and CopilotKit matchers/proxy and streaming-only flush configuration. Keep ordinary `/api/*` and `/readyz` API routing.

- [ ] **Step 5: Include and validate the reset command**

The release script already copies `packaging/bin`; add `packaging/bin/reset-data` to `check:scripts` so `bash -n` validates it. Extend deployment asset tests to verify release and executable permissions.

- [ ] **Step 6: Run deployment tests and shell checks**

Run: `uv run --frozen pytest -q apps/api/tests/test_reset_script.py apps/api/tests/test_deployment_assets.py`

Run: `npm run check:scripts`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packaging/bin/reset-data apps/api/tests/test_reset_script.py scripts/build-release.sh package.json .env.example contrib/Caddyfile apps/api/tests/test_deployment_assets.py
git commit -m "build: add explicit destructive data reset"
```

### Task 11: Documentation And Source-Surface Cleanup

**Files:**
- Modify: `docs/deployment.md`
- Modify: `docs/authentication.md`
- Modify: `docs/workflow.md`
- Modify: `docs/development.md`
- Modify: `docs/incus.md`
- Modify: `docs/frostyard-incus.md`
- Modify: `site/content/operations/deployment.md`
- Modify: `site/content/operations/authentication.md`
- Modify: `site/content/operations/incus.md`
- Modify: `site/content/reference/configuration.md`
- Modify: `site/content/reference/development.md`
- Modify: `site/content/workflows/specification-delivery.md`
- Modify: `apps/api/tests/test_frontend_contracts.py`

**Interfaces:**
- Documents: REST start/retry/decision flow, SQLite pending actions, restart failure behavior, polling, and destructive upgrade.
- Removes: all operational references to AG-UI, CopilotKit, checkpoints, snapshots, threads, and interrupt restoration.

- [ ] **Step 1: Add repository-wide absence assertions**

Extend contract tests to scan runtime source, manifests, env examples, Caddy, and operator docs. Allow historical mentions only in the approved design and implementation-plan documents.

- [ ] **Step 2: Run contract and site tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py && npm test --prefix site`

Expected: FAIL on obsolete documentation references.

- [ ] **Step 3: Rewrite workflow and deployment documentation**

Describe pending actions as the durable approval mechanism, browser-independent background work, startup failure/retry, and three-second polling. Add the exact breaking-upgrade procedure:

```bash
sudo systemctl stop mafia.target
sudo -u mafia env MAFIA_DATA_DIR=/var/lib/mafia /opt/mafia/bin/reset-data --confirm-destructive-reset
sudo -u mafia env MAFIA_DATA_DIR=/var/lib/mafia /opt/mafia/.venv/bin/python -m alembic -c /opt/mafia/alembic.ini upgrade head
sudo systemctl start mafia.target
```

- [ ] **Step 4: Run contract and site tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py && npm test --prefix site`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs site/content apps/api/tests/test_frontend_contracts.py
git commit -m "docs: describe sqlite workflow control plane"
```

### Task 12: Full Verification, Fresh Reset, And Live Smoke Test

**Files:**
- Modify only files required to correct failures found by these checks.
- Do not modify or stage: `apps/web/next-env.d.ts`.

**Interfaces:**
- Verifies every success criterion in `docs/superpowers/specs/2026-07-30-remove-ag-ui-design.md`.

- [ ] **Step 1: Scan removed runtime surfaces**

Run:

```bash
rg -n 'AG-UI|AGUI|ag-ui|agui|CopilotKit|copilotkit|WorkflowBuilder|CheckpointStorage|request_info|useAgent|useInterrupt|thread_id|checkpoints_dir' apps pyproject.toml uv.lock package.json .env.example contrib packaging docs site/content
```

Expected: no runtime/config/dependency matches; only intentional historical text in the approved spec/plan may remain.

- [ ] **Step 2: Run the complete repository check**

Run: `npm run check`

Expected: Ruff PASS, Pyright PASS, all API tests PASS, web lint/type/tests/build PASS, site tests PASS, and shell syntax PASS.

- [ ] **Step 3: Verify a fresh migration**

Run:

```bash
tmp=$(mktemp -d)
MAFIA_DATA_DIR="$tmp" uv run --frozen alembic -c alembic.ini upgrade head
MAFIA_DATA_DIR="$tmp" uv run --frozen python -c 'from mafia.db.models import PendingAction; print(PendingAction.__tablename__)'
rm -rf "$tmp"
```

Expected: migration exits 0 and prints `pending_actions`.

- [ ] **Step 4: Review the destructive target before resetting development data**

Run: `printf '%s\n' "${MAFIA_DATA_DIR:?MAFIA_DATA_DIR must be explicit}"`

Expected: the printed path is the intended development runtime directory, not `/`, `.`, `..`, the repository root, or a home directory. Stop and obtain user confirmation if it is not clearly the disposable MAFIA runtime directory.

- [ ] **Step 5: Reset and restart the development instance**

Stop the local API/web processes using the repository's normal supervisor, run `packaging/bin/reset-data --confirm-destructive-reset` with the verified `MAFIA_DATA_DIR`, run Alembic upgrade, then start the services. Do not use this step against any non-development instance.

- [ ] **Step 6: Exercise the live REST workflow**

Create a project and run through the UI, click Start, wait for a specification pending action, submit Refine once, wait for the new revision, submit Accept, and confirm plan work starts without keeping the initiating browser request open. Restart the API during a disposable working run, confirm it becomes Failed, click Retry, and confirm exactly one operation starts.

- [ ] **Step 7: Inspect final diff and status**

Run: `git status --short && git diff --check && git diff --stat`

Expected: only intended implementation, lockfile, migration, test, reset, and documentation changes; no staged or modified `apps/web/next-env.d.ts` from this work.

- [ ] **Step 8: Commit verification fixes if any**

```bash
git add <only-files-changed-to-fix-verification>
git commit -m "test: complete control plane verification"
```

Skip this commit when verification required no code changes.
