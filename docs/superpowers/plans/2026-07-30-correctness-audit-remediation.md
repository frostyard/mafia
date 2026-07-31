# Correctness Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 20 approved correctness findings across workflow lifecycle, frontend/backend contracts, UI behavior, settings, and deployment.

**Architecture:** Keep the existing FastAPI and Next.js boundaries. Harden backend transitions, centralize frontend workflow-state semantics in one typed module, and add contract/regression tests around every changed boundary. Preserve all public API and persisted-data shapes.

**Tech Stack:** Python 3.11-3.13, FastAPI, Pydantic Settings, SQLAlchemy asyncio, pytest, Next.js 16, React 19, TypeScript 5.9, Vitest, Testing Library, CSS, Bash/systemd.

## Global Constraints

- Keep endpoint paths, response shapes, database schema, persisted workflow state, and project TOML schema compatible.
- Keep MAFIA single-process; explicitly configure and document one API worker.
- Use test-driven development for each behavioral correction.
- Do not overwrite unrelated worktree changes.
- Do not commit unless the user explicitly requests commits.

---

### Task 1: Typed Frontend Workflow Contracts

**Files:**
- Create: `apps/web/src/lib/workflow-state.ts`
- Modify: `apps/web/src/lib/types.ts:32-53,80-102,131-149`
- Modify: `apps/web/src/app/page.tsx:5-14`
- Modify: `apps/web/src/components/workflow-panel.tsx:159-234`
- Modify: `apps/web/src/components/run-cards.tsx`
- Test: `apps/web/src/lib/workflow-state.test.ts`
- Test: `apps/api/tests/test_frontend_contracts.py`

**Interfaces:**
- Produces: `RUN_STATES`, `PHASE_STATES`, `OPERATION_STATUSES`, `RunState`, `PhaseState`, `OperationStatus`.
- Produces: `isDecisionState(state)`, `isTerminalState(state)`, `runStateTone(state)`, `phaseStateTone(state)`.
- Consumes: backend enum values from `apps/api/src/mafia/domain/enums.py`.

- [ ] **Step 1: Write failing Vitest coverage for shared state semantics**

```ts
import { describe, expect, it } from "vitest";
import {
  RUN_STATES,
  isDecisionState,
  isTerminalState,
  runStateTone,
} from "@/lib/workflow-state";

describe("workflow state contract", () => {
  it("classifies every operator decision state", () => {
    expect(RUN_STATES.filter(isDecisionState)).toEqual([
      "awaiting_spec_decision",
      "awaiting_plan_decision",
      "ready_for_phase",
      "awaiting_pr_review_decision",
    ]);
  });

  it("classifies terminal states and gives every state a tone", () => {
    expect(["completed", "failed", "cancelled"].every(isTerminalState)).toBe(true);
    expect(RUN_STATES.every((state) => runStateTone(state) !== undefined)).toBe(true);
  });
});
```

- [ ] **Step 2: Write a failing backend contract test that parses exported TypeScript arrays**

```python
import re
from pathlib import Path

from mafia.domain.enums import PhaseState, RunState


def _typescript_values(source: str, name: str) -> set[str]:
    match = re.search(rf"export const {name} = \[(.*?)\] as const", source, re.S)
    assert match is not None
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_frontend_workflow_states_match_backend() -> None:
    source = Path("apps/web/src/lib/workflow-state.ts").read_text()
    assert _typescript_values(source, "RUN_STATES") == {state.value for state in RunState}
    assert _typescript_values(source, "PHASE_STATES") == {state.value for state in PhaseState}
```

- [ ] **Step 3: Run both tests and verify they fail because the module does not exist**

Run: `npm test --prefix apps/web -- --run src/lib/workflow-state.test.ts`

Run: `uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py`

Expected: both fail due to the missing contract module.

- [ ] **Step 4: Implement the typed state module and use it in existing interfaces/helpers**

```ts
export const RUN_STATES = [
  "intake", "generating_spec", "awaiting_spec_decision", "grounding_plan",
  "generating_plan", "reviewing_plan", "adjudicating_plan", "persisting_plan",
  "awaiting_plan_decision", "ready_for_phase", "executing_phase",
  "reviewing_implementation", "adjudicating_implementation",
  "remediating_implementation", "verifying_remediation", "pr_open",
  "waiting_for_merge", "regrounding", "grounding_pr_review", "reviewing_pr",
  "consolidating_pr_review", "awaiting_pr_review_decision", "posting_pr_review",
  "completed", "failed", "cancelled",
] as const;

export const PHASE_STATES = [
  "pending", "ready", "executing", "waiting_for_merge", "merged", "failed",
] as const;

export const OPERATION_STATUSES = [
  "running", "completed", "failed", "timed_out", "cancelled",
] as const;

export type RunState = (typeof RUN_STATES)[number];
export type PhaseState = (typeof PHASE_STATES)[number];
export type OperationStatus = (typeof OPERATION_STATUSES)[number];
export type StateTone = "idle" | "working" | "decision" | "success" | "danger" | "external";

const decisionStates = new Set<RunState>([
  "awaiting_spec_decision", "awaiting_plan_decision", "ready_for_phase",
  "awaiting_pr_review_decision",
]);
const terminalStates = new Set<RunState>(["completed", "failed", "cancelled"]);

export const isDecisionState = (state: RunState) => decisionStates.has(state);
export const isTerminalState = (state: RunState) => terminalStates.has(state);
```

Complete explicit `runStateTone` and `phaseStateTone` switch statements with a `never` exhaustiveness guard. Update `Run.state`, `Phase.status`, and `Operation.status` in `types.ts`. Replace dashboard substring matching and workflow-panel decision literals with `isDecisionState`.

- [ ] **Step 5: Run focused tests and typecheck**

Run: `npm test --prefix apps/web -- --run src/lib/workflow-state.test.ts && npm run typecheck --prefix apps/web && uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py`

Expected: all pass.

### Task 2: Idempotent Phase Decisions

**Files:**
- Modify: `apps/api/src/mafia/workflows/run_workflow.py:1289-1311`
- Test: `apps/api/tests/test_workflow_decisions.py`

**Interfaces:**
- Consumes: `PhaseDecisionRequest`, `RunState.READY_FOR_PHASE`, `PhaseState.READY`.
- Produces: stale phase responses yield output and perform no transition or execution.

- [ ] **Step 1: Add failing tests for stale start and cancel responses**

```python
@pytest.mark.asyncio
async def test_stale_phase_start_is_ignored(session, workflow_context, phase) -> None:
    phase.status = PhaseState.EXECUTING
    await session.commit()
    await executor.decide_phase(request_for(phase), {"action": "start"}, workflow_context)
    workflow_context.yield_output.assert_awaited_once()
    execute_phase.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_phase_cancel_is_ignored(session, workflow_context, phase) -> None:
    phase.run.state = RunState.EXECUTING_PHASE
    await session.commit()
    await executor.decide_phase(request_for(phase), {"action": "cancel"}, workflow_context)
    workflow_context.yield_output.assert_awaited_once()
    assert phase.run.state == RunState.EXECUTING_PHASE
```

Use the existing fixtures and monkeypatch patterns in `test_workflow_decisions.py`; do not introduce parallel fixture infrastructure.

- [ ] **Step 2: Run the focused tests and verify the current handler raises or calls execution**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_decisions.py -k 'stale_phase'`

Expected: failure.

- [ ] **Step 3: Add the phase/run guard and cancellation audit record**

```python
async with SessionFactory() as session:
    run = await get_run(session, original_request.run_id)
    phase = await session.get(Phase, original_request.phase_id)
    if (
        phase is None
        or phase.run_id != run.id
        or run.state != RunState.READY_FOR_PHASE
        or phase.status != PhaseState.READY
    ):
        await ctx.yield_output("Ignored a stale phase decision because the phase is no longer ready.")
        return
```

For cancel, add a `Decision` with `DecisionType.CANCEL` before transitioning, following the artifact decision implementation. For start, leave `execute_phase` unchanged after the guard.

- [ ] **Step 4: Run workflow decision and state-machine tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_workflow_decisions.py apps/api/tests/test_state_machine.py`

Expected: all pass.

### Task 3: Stop-Before-Transition Run Controls

**Files:**
- Modify: `apps/api/src/mafia/services/activity.py:241-359`
- Modify: `apps/api/src/mafia/services/operations.py:306-314`
- Modify: `apps/api/src/mafia/config.py:12-28`
- Modify: `apps/api/src/mafia/cli.py:6-13`
- Modify: `docs/deployment.md:44-70`
- Test: `apps/api/tests/test_operations.py`
- Test: `apps/api/tests/test_config.py`

**Interfaces:**
- Produces: `_stop_active_work(run_id: str, reason: str) -> None`.
- Produces: `Settings.api_workers: Literal[1]` and explicit `workers=1` Uvicorn launch.

- [ ] **Step 1: Add failing tests for cancellation timeout state preservation**

```python
@pytest.mark.asyncio
async def test_cancel_timeout_keeps_working_state(monkeypatch, active_run) -> None:
    monkeypatch.setattr(activity, "_wait_for_active_work", AsyncMock(side_effect=RunControlError("still stopping")))
    with pytest.raises(RunControlError, match="still stopping"):
        await activity.cancel_run(active_run.id)
    async with SessionFactory() as session:
        persisted = await get_run(session, active_run.id)
        assert persisted.state == RunState.GENERATING_SPEC
```

Add equivalent stalled-retry and reset tests asserting no replacement state is published while work remains active.

- [ ] **Step 2: Run focused control tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_operations.py -k 'timeout or stopping'`

Expected: tests observe premature `cancelled`/`failed` transitions.

- [ ] **Step 3: Implement stop-first control flow**

```python
async def _stop_active_work(run_id: str, reason: str) -> None:
    cancel_active_work(run_id)
    await _wait_for_active_work(run_id)
    await _close_running_operations(run_id, reason)
```

Call this helper before terminal/reset transitions. Re-read the run after stopping and use its current version for the transition. Do not permit retry/reset continuation after `_wait_for_active_work` raises.

- [ ] **Step 4: Enforce the supported process model**

```python
api_workers: Literal[1] = 1
```

Pass `workers=settings.api_workers` to `uvicorn.run`. Document that external multi-worker Uvicorn/Gunicorn launch is unsupported because active task cancellation is process-local. Add a validation test showing `Settings(api_workers=2)` fails.

- [ ] **Step 5: Run control, restart, and config tests**

Run: `uv run --frozen pytest -q apps/api/tests/test_operations.py apps/api/tests/test_config.py`

Expected: all pass in an isolated settings environment established by Task 8; until then, run from `/tmp/opencode` with `uv run --project "$PWD"` if the repository `.env` affects config tests.

### Task 4: Decision Prompt And Run Loading

**Files:**
- Modify: `apps/web/src/components/workflow-panel.tsx:119-149`
- Modify: `apps/web/src/app/runs/[id]/page.tsx:179-208`
- Modify: `apps/web/src/components/evidence-drawer.tsx:1-27`
- Test: `apps/web/src/components/workflow-panel.test.tsx`
- Test: `apps/web/src/components/evidence-drawer.test.tsx`
- Test: `apps/web/src/app/runs/[id]/page.test.tsx`

**Interfaces:**
- Produces: `interruptPrompt(interrupt: unknown): string`.
- Produces: `EvidenceDrawer` accepts optional localized `error` text.

- [ ] **Step 1: Add failing prompt extraction tests**

```tsx
it("renders the workflow request prompt from Agent Framework metadata", () => {
  mockedInterrupt = {
    metadata: {
      agent_framework: {
        request_type: "PhaseDecisionRequest",
        data: { prompt: "Start phase 2 using repository validation?" },
      },
    },
  };
  render(<WorkflowPanel {...props} />);
  expect(screen.getByText("Start phase 2 using repository validation?")).toBeTruthy();
});
```

- [ ] **Step 2: Add a failing run-page test where evidence rejects**

Mock `getRun` and `getRunActivity` to resolve and `getEvidence` to reject. Assert the repository heading and a localized evidence warning render instead of “We could not load this run.”

- [ ] **Step 3: Run focused tests and verify failure**

Run: `npm test --prefix apps/web -- --run src/components/workflow-panel.test.tsx src/app/runs/'[id]'/page.test.tsx`

Expected: generic decision text and unavailable run page.

- [ ] **Step 4: Implement metadata extraction and independent evidence loading**

```ts
function interruptPrompt(interrupt: unknown): string {
  if (typeof interrupt !== "object" || interrupt === null) return DEFAULT_PROMPT;
  const value = interrupt as Record<string, unknown>;
  const framework = metadataFramework(value.metadata);
  const data = framework?.data;
  if (typeof data === "object" && data !== null && typeof (data as Record<string, unknown>).prompt === "string") {
    return (data as Record<string, string>).prompt;
  }
  return typeof value.message === "string" ? value.message : DEFAULT_PROMPT;
}
```

Load run/activity together as essential data. Resolve evidence separately into `{evidence, evidenceError}` and render the drawer warning without blocking `RunDetailView`.

- [ ] **Step 5: Fix nullable evidence ranges with a regression test**

Render an item with `line_start: 5, line_end: null` and assert `file.py:5` appears while `file.py:5-null` does not. Only append `-${line_end}` when `line_end != null && line_end !== line_start`.

- [ ] **Step 6: Run focused tests and typecheck**

Run: `npm test --prefix apps/web -- --run src/components/workflow-panel.test.tsx src/components/evidence-drawer.test.tsx src/app/runs/'[id]'/page.test.tsx && npm run typecheck --prefix apps/web`

Expected: all pass.

### Task 5: Project And Model Error States

**Files:**
- Modify: `apps/web/src/app/projects/page.tsx:1-19`
- Modify: `apps/web/src/app/projects/[id]/page.tsx:1-34`
- Modify: `apps/web/src/app/not-found.tsx`
- Modify: `apps/web/src/app/runs/new/page.tsx:1-22`
- Modify: `apps/web/src/components/run-form.tsx:37-88,234-280`
- Test: `apps/web/src/components/run-form.test.tsx`
- Test: `apps/web/src/app/projects/page.test.tsx`
- Test: `apps/web/src/app/projects/[id]/page.test.tsx`

**Interfaces:**
- Produces: `RunForm` prop `modelLoadError?: string` separate from `modelAvailability`.
- Consumes: `ApiError.code` for explicit `project_not_found` detection.

- [ ] **Step 1: Add failing page/error-state tests**

Add tests asserting project-list API failure renders “Project settings are unavailable,” project detail only calls `notFound()` for `project_not_found`, and model API failure renders “Model availability could not be loaded” rather than “No required models.”

- [ ] **Step 2: Run focused tests and verify current behavior fails**

Run: `npm test --prefix apps/web -- --run src/components/run-form.test.tsx src/app/projects/page.test.tsx src/app/projects/'[id]'/page.test.tsx`

Expected: missing or incorrect error states.

- [ ] **Step 3: Implement explicit error branches**

```tsx
let modelLoadError: string | undefined;
try {
  modelAvailability = await getModelAvailability();
} catch (error) {
  modelLoadError = (error as ApiError).message ?? "Model availability could not be loaded.";
}
```

Apply the same typed error handling to projects. Give the project route its own not-found copy instead of the shared “workflow” noun, either through route-local rendering or neutral shared copy.

- [ ] **Step 4: Add a visible retry path**

Use a normal link/button that reloads the current route for SSR load failures. Keep submission disabled while no valid model pair is available, but distinguish empty capability data from transport failure.

- [ ] **Step 5: Run focused tests and web typecheck**

Run: `npm test --prefix apps/web -- --run src/components/run-form.test.tsx src/app/projects/page.test.tsx src/app/projects/'[id]'/page.test.tsx && npm run typecheck --prefix apps/web`

Expected: all pass.

### Task 6: Lossless Project Configuration Editing

**Files:**
- Modify: `apps/web/src/components/project-settings-form.tsx:37-224`
- Test: `apps/web/src/components/project-settings-form.test.tsx`

**Interfaces:**
- Produces: explicit raw-TOML editing state and `discardRawToml()` behavior.
- Produces: timeout draft strings validated to integer values from 1 through 3600.

- [ ] **Step 1: Add failing tests for raw TOML preservation and timeout validation**

```tsx
it("does not discard pasted TOML when a structured control is used", () => {
  render(<ProjectSettingsForm project={project} />);
  fireEvent.change(screen.getByLabelText("Host .mafia.toml"), {
    target: { value: "version = 1\n# custom\n" },
  });
  expect(screen.getByLabelText("Name")).toBeDisabled();
  expect(screen.getByDisplayValue(/# custom/)).toBeTruthy();
});

it.each(["", "0", "3601", "1.5"])("rejects timeout %s", async (value) => {
  render(<ProjectSettingsForm project={project} />);
  fireEvent.change(screen.getByLabelText("Timeout (seconds)"), {
    target: { value },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save project settings" }));
  expect(updateProjectConfiguration).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toMatch(/whole number from 1 to 3600/i);
});
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `npm test --prefix apps/web -- --run src/components/project-settings-form.test.tsx`

Expected: structured edits erase the override and invalid timeouts reach generated TOML.

- [ ] **Step 3: Implement explicit raw mode and timeout drafts**

Store timeout inputs as `string[]` aligned with commands. Parse only during structured TOML generation or submission:

```ts
function parseTimeout(value: string): number | undefined {
  if (!/^\d+$/.test(value)) return undefined;
  const timeout = Number(value);
  return timeout >= 1 && timeout <= 3600 ? timeout : undefined;
}
```

When `tomlOverride !== undefined`, disable structured controls and show an explicit “Discard raw TOML and edit fields” button. That button is the only action that clears the override. Saving raw TOML sends it unchanged for backend validation/normalization.

- [ ] **Step 4: Run the component tests and accessibility queries**

Run: `npm test --prefix apps/web -- --run src/components/project-settings-form.test.tsx`

Expected: all pass.

### Task 7: Timeline, Refresh, Polling, Styling, And Dates

**Files:**
- Modify: `apps/web/src/lib/workflow-state.ts`
- Modify: `apps/web/src/components/stage-timeline.tsx:1-77`
- Modify: `apps/web/src/app/runs/[id]/page.tsx:117-173`
- Modify: `apps/web/src/components/visibility-rail.tsx:33-47,238-281`
- Modify: `apps/web/src/components/artifact-tabs.tsx:60-80`
- Modify: `apps/web/src/app/globals.css:225-274,990-1036`
- Test: `apps/web/src/components/stage-timeline.test.tsx`
- Test: `apps/web/src/components/visibility-rail.test.tsx`
- Test: `apps/web/src/components/artifact-tabs.test.tsx`

**Interfaces:**
- Produces: `stageForState(state, workflowType)`, `lastMeaningfulState(activity)`.
- Produces: deterministic `formatTimestamp(value)` using an explicit locale and `timeZone: "UTC"`.

- [ ] **Step 1: Add exhaustive stage and terminal-stage tests**

Iterate over `RUN_STATES`, asserting each state maps to a valid stage for its workflow. For failed/cancelled activity, provide events ending in a terminal transition and assert the prior state’s stage remains current with terminal styling.

- [ ] **Step 2: Add failing polling and refresh visibility tests**

Use fake timers to assert terminal initial activity schedules no API poll. Render run detail states and assert “Refresh PR status” appears only for `waiting_for_merge` specification runs.

- [ ] **Step 3: Add deterministic timestamp tests**

Assert `2026-07-30T12:00:00Z` renders identically under differing process timezone settings by routing all client timestamp display through one formatter with `timeZone: "UTC"`.

- [ ] **Step 4: Run focused tests and verify failure**

Run: `npm test --prefix apps/web -- --run src/components/stage-timeline.test.tsx src/components/visibility-rail.test.tsx src/components/artifact-tabs.test.tsx`

Expected: terminal stage, polling, and timezone assertions fail.

- [ ] **Step 5: Implement explicit stage mapping and terminal event derivation**

Replace substring heuristics with exhaustive maps keyed by `RunState`. Pass `activity.events` into `StageTimeline`; when state is terminal, scan events newest-first for the last non-terminal `to_state` or `from_state` and map that state.

- [ ] **Step 6: Stop terminal polling and restrict refresh rendering**

Return from the polling effect without scheduling a timer when `isTerminalState(initialActivity.state)`; after each response, only schedule the next poll when the next state is non-terminal. Render `RefreshPrStatus` only when `run.state === "waiting_for_merge"`.

- [ ] **Step 7: Complete visual mappings**

Use tone classes (`tone-idle`, `tone-working`, `tone-decision`, `tone-success`, `tone-danger`, `tone-external`) rather than one CSS selector per run state, while preserving current class names if tests or markup rely on them. Ensure every state helper returns one tone and add explicit rules for all activity modes.

- [ ] **Step 8: Run focused tests, lint, and typecheck**

Run: `npm test --prefix apps/web -- --run src/components/stage-timeline.test.tsx src/components/visibility-rail.test.tsx src/components/artifact-tabs.test.tsx && npm run lint --prefix apps/web -- --quiet && npm run typecheck --prefix apps/web`

Expected: all pass.

### Task 8: Hermetic Settings Precedence

**Files:**
- Modify: `apps/api/src/mafia/config.py:1-69`
- Modify: `apps/api/tests/test_config.py:1-121`

**Interfaces:**
- Produces: explicitly supplied settings fields replace same-named environment/dotenv values, including mappings.
- Produces: config tests isolated from repository `.env` with `_env_file=None` and `_env_prefix="TEST_MAFIA_"`.

- [ ] **Step 1: Add a failing explicit-replacement test**

```python
def test_explicit_model_pairs_replace_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_MAFIA_MODEL_PAIRS", '{"old":"reviewer"}')
    settings = Settings(
        _env_file=None,
        _env_prefix="TEST_MAFIA_",
        model_pairs={"new": "peer"},
    )
    assert settings.model_pairs == {"new": "peer"}
```

Keep the empty-map validation test and assert it raises even when the environment contains a non-empty mapping.

- [ ] **Step 2: Run config tests in the repository checkout and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_config.py`

Expected: explicit mapping values merge with environment values before the fix.

- [ ] **Step 3: Customize settings sources to remove explicitly supplied fields from lower-priority mapping sources**

Implement `settings_customise_sources` using the `init_settings.init_kwargs` keys. Wrap `env_settings` and `dotenv_settings` so their returned dictionaries omit fields present in init settings before Pydantic performs deep merging. Preserve normal environment loading when no explicit value was supplied.

```python
explicit = set(init_settings.init_kwargs)

def without_explicit(source):
    return lambda: {
        key: value for key, value in source().items() if key not in explicit
    }
```

Return sources in normal precedence order: init, filtered environment, filtered dotenv, file secrets.

- [ ] **Step 4: Isolate config tests from the repository `.env`**

Use `_env_file=None` and `_env_prefix="TEST_MAFIA_"` for tests that do not intentionally exercise production `MAFIA_` environment loading. Update the JSON-environment test to set `TEST_MAFIA_MODEL_PAIRS`.

- [ ] **Step 5: Run config tests both inside and outside the repository working directory**

Run: `uv run --frozen pytest -q apps/api/tests/test_config.py`

Run from `/tmp/opencode`: `uv run --project "/home/bjk/projects/frostyard/mafia" --frozen pytest -q "/home/bjk/projects/frostyard/mafia/apps/api/tests/test_config.py"`

Expected: both runs pass with identical counts.

### Task 9: Deployment And Release Correctness

**Files:**
- Modify: `contrib/systemd/mafia-api.service:11-23`
- Modify: `contrib/incus/personal.env.example:1-28`
- Modify: `contrib/incus/frostyard.env.example:1-35`
- Modify: `docs/deployment.md:24-93`
- Modify: `scripts/build-release.sh:46-58`
- Test: `apps/api/tests/test_deployment_assets.py`

**Interfaces:**
- Produces: systemd API environment fixed to `MAFIA_DATA_DIR=/var/lib/mafia` unless explicitly overridden by the deployment environment file according to documented precedence.
- Produces: release archives without `__pycache__` or `*.py[co]`.

- [ ] **Step 1: Add failing static deployment tests**

```python
def test_systemd_uses_persistent_data_directory() -> None:
    unit = Path("contrib/systemd/mafia-api.service").read_text()
    assert "Environment=MAFIA_DATA_DIR=/var/lib/mafia" in unit


@pytest.mark.parametrize("path", [
    "contrib/incus/personal.env.example",
    "contrib/incus/frostyard.env.example",
])
def test_incus_examples_do_not_advertise_ignored_execution_mode(path: str) -> None:
    assert "MAFIA_EXECUTION_MODE" not in Path(path).read_text()
```

Add a test that inspects `scripts/build-release.sh` for an exclusion/removal step covering `__pycache__`, `*.pyc`, and `*.pyo`.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --frozen pytest -q apps/api/tests/test_deployment_assets.py`

Expected: all new assertions fail.

- [ ] **Step 3: Correct deployment assets**

Add `Environment=MAFIA_DATA_DIR=/var/lib/mafia` before `EnvironmentFile` so the environment file can remain the documented operator override. Remove `MAFIA_EXECUTION_MODE` from Incus examples and add nearby comments that execution mode is configured per project.

- [ ] **Step 4: Exclude caches from the release tree**

After copying migrations, remove copied caches using a portable command available in the release build environment:

```bash
find "$staging/$release_name/apps/api/migrations" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$staging/$release_name/apps/api/migrations" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
```

The production edit may use `find` because this is a build script, not an interactive repository search.

- [ ] **Step 5: Update deployment documentation and run checks**

Document the persistent default and single-process requirement. Run:

`uv run --frozen pytest -q apps/api/tests/test_deployment_assets.py && npm run check:scripts`

Expected: tests and shell syntax pass.

### Task 10: Full Verification And Review

**Files:**
- Review all files changed by Tasks 1-9.
- Update tests only when verification reveals an actual uncovered defect; do not weaken assertions.

**Interfaces:**
- Consumes: every deliverable above.
- Produces: one verified, review-ready working tree covering all 20 findings.

- [ ] **Step 1: Run the complete API gate**

Run: `npm run check:api`

Expected: Ruff, Pyright, and all API tests pass.

- [ ] **Step 2: Run the complete frontend gate**

Run: `npm run check:web`

Expected: ESLint, TypeScript, all Vitest tests, and the Next.js production build pass.

- [ ] **Step 3: Run documentation and script gates**

Run: `npm run check:site && npm run check:scripts`

Expected: Astro/Pagefind build and tests pass; shell syntax passes.

- [ ] **Step 4: Run the root gate from the configured checkout**

Run: `npm run check`

Expected: all checks pass even when a repository `.env` exists.

- [ ] **Step 5: Review the final diff against finding coverage**

Run: `git diff --check && git status --short && git diff --stat`

Expected: no whitespace errors; only intended implementation, tests, docs, design, and plan files are changed.

- [ ] **Step 6: Request adversarial code review**

Dispatch a reviewer with the approved design, this plan, and the complete diff. Resolve every critical or important finding, then repeat the affected focused test and `npm run check` before reporting completion.
