# Task 12 Report

- Implementation commit: `572483a fix: retain run locks through queued handoffs`
- Regression: `uv run --frozen pytest -q apps/api/tests/test_operations.py -k run_work_lock_keeps_queued_callers` passed (`1 passed`). The test deterministically holds a queued waiter during lock handoff, verifies that holder, waiter, and contender use one lock without overlap, and checks registry cleanup after the last exit.
- Focused coverage: `uv run --frozen pytest -q apps/api/tests/test_operations.py apps/api/tests/test_run_control.py apps/api/tests/test_run_control_api.py` passed (`40 passed`).
- Full validation: `npm run check` passed: API lint/typecheck/pytest (`252 passed`), web lint/typecheck/vitest/build, site build/tests (`6 passed`), and shell syntax checks.
