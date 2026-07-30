# Task 1 Report: Typed Frontend Workflow Contracts

## Files Changed

- `apps/web/src/lib/workflow-state.ts`: added canonical run, phase, and operation state arrays; derived types; decision and terminal classifiers; exhaustive run and phase tone mappings.
- `apps/web/src/lib/workflow-state.test.ts`: added shared workflow-state semantics coverage.
- `apps/api/tests/test_frontend_contracts.py`: added backend/frontend enum-parity coverage.
- `apps/web/src/lib/types.ts`: constrained `Run.state`, `Phase.status`, and `Operation.status` to the canonical types.
- `apps/web/src/app/page.tsx`: replaced terminal and decision string matching with shared classifiers.
- `apps/web/src/components/workflow-panel.tsx`: replaced decision-state literals with `isDecisionState` and constrained its run-state props.
- `apps/web/src/components/run-cards.tsx`: constrained state badge inputs and exposes the canonical run tone.
- `apps/web/src/lib/activity-refresh.test.ts`: aligned the affected operation fixture helper with the narrowed operation-status contract.

## Test Evidence

### Red

`npm test --prefix apps/web -- --run src/lib/workflow-state.test.ts`

Result: failed as expected before implementation. Vitest could not resolve `@/lib/workflow-state` because the module did not exist.

`uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py`

Result: failed as expected before implementation with `FileNotFoundError` for `apps/web/src/lib/workflow-state.ts`.

### Green

`npm test --prefix apps/web -- --run src/lib/workflow-state.test.ts && npm run typecheck --prefix apps/web && uv run --frozen pytest -q apps/api/tests/test_frontend_contracts.py`

Result: passed. Vitest reported 1 test file and 2 tests passed; TypeScript exited successfully with `tsc --noEmit`; pytest reported 1 passed in 0.01s.

## Commit

- `f9dbe24 feat: centralize workflow state contracts`

## Self-Review

- The backend parity test verifies the frontend `RUN_STATES` and `PHASE_STATES` arrays are exact set matches for `RunState` and `PhaseState` in `apps/api/src/mafia/domain/enums.py`.
- `RUN_STATES`, `PHASE_STATES`, and `OPERATION_STATUSES` are `as const`, and their types are derived directly from those arrays.
- `runStateTone` and `phaseStateTone` enumerate every member of their respective unions and route any future unhandled member through a `never` guard.
- Dashboard and workflow controls now use the shared decision/terminal semantics rather than duplicated or substring-based checks.
- No endpoint paths, response shapes, database schema, persisted workflow state, or TOML schema changed.

## Concerns

None.
