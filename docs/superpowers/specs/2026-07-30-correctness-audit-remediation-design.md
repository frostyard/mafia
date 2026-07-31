# Correctness Audit Remediation Design

## Goal

Resolve all 20 findings from the frontend, backend, integration, configuration,
and deployment audit without changing the public API or invalidating persisted
runs and project configuration.

## Approach

Use targeted hardening at the existing boundaries. Keep FastAPI as the source of
truth for workflow state, add explicit matching state contracts and shared state
helpers in the frontend, and verify parity with contract tests. Avoid a generated
API client or presentation-specific backend view models because either would add
substantial unrelated churn.

MAFIA remains a single-process application. The shipped launcher and deployment
documentation will state and enforce that constraint instead of adding distributed
task ownership and cancellation.

## Backend Lifecycle

Phase decisions will use the same stale-response behavior as artifact and pull
request review decisions. Before starting or cancelling a phase, the handler will
confirm that the request belongs to the run, the run is `ready_for_phase`, and the
phase is `ready`. A stale response will yield an explanatory no-op result instead
of raising. Phase cancellation will receive the same durable decision audit record
as other decision types.

Cancellation, stalled retry, and specification reset will stop active work before
publishing a terminal or replacement state. The control operation will request
cancellation and wait for task termination. Only after termination will it close
running operations and transition the run. If work does not stop within the bound,
the original run state remains authoritative and the endpoint reports that work is
still stopping. This prevents a new attempt from overlapping old work.

The in-memory active-task registry remains process-local. Startup and deployment
surfaces will make the single-process requirement explicit and reject unsupported
multi-worker configuration where it can be detected. Merge monitoring remains one
task in that process.

## Frontend Contracts

The frontend will define explicit `RunState`, `PhaseState`, and `OperationStatus`
unions matching backend enums and operation schemas. Shared helpers will own:

- decision and terminal state classification;
- dashboard counts;
- workflow action availability;
- specification and pull-request-review stage mapping;
- badge/status presentation categories; and
- terminal polling behavior.

Components will consume these helpers rather than repeat string comparisons. A
contract test will compare the frontend vocabulary with backend enum or OpenAPI
values so backend changes cannot silently hide controls or reset presentation.

## Decision And Refresh Flow

Decision cards will extract their prompt from persisted action metadata, with
generic copy as a fallback. Request-type extraction remains metadata-based.

The run page will treat the run and activity responses as essential while evidence
is optional. Evidence failure will produce a localized unavailable state rather
than hide the run. The pull-request refresh action will only render while a
specification workflow is `waiting_for_merge`, the only state where reconciliation
is meaningful.

Activity polling will stop for completed, failed, and cancelled runs. Failed and
cancelled timelines will derive their last meaningful non-terminal state from
activity events and preserve that stage with terminal styling.

## UI Consistency And Errors

Dashboard decision counts will include phase approval. Every backend run state,
phase state, operation status, and activity mode will map to an intentional visual
category, including pull-request-review and implementation-review states.

Projects and model availability will distinguish not-found, unavailable, and
valid-empty outcomes. The project list will render a local API-unavailable state;
project detail will call `notFound()` only for an explicit project-not-found error.
The new-run page will pass model-loading failure separately from an empty model
list and provide accurate retry guidance.

Dates rendered by client components will use deterministic formatting that cannot
disagree between server and browser hydration. Evidence ranges will omit the end
suffix when `line_end` is null.

## Project Configuration Editing

Raw TOML and structured controls will be explicit editing modes. Entering raw TOML
mode preserves that text; structured controls cannot silently overwrite it. The
user must explicitly apply or discard raw TOML before returning to structured
editing.

Timeout fields will retain textual input while being edited. Saving requires an
integer from 1 through 3600, and invalid values will produce a field error rather
than generate `0`, `NaN`, or invalid TOML.

## Settings And Deployment

Settings construction will ensure an explicitly supplied mapping replaces, rather
than deep-merges with, an environment mapping. Unit tests will opt out of repository
`.env` loading so their result does not depend on the developer or deployment
environment. Empty explicit model-pair mappings must still fail validation.

The systemd deployment path will set `MAFIA_DATA_DIR` to persistent storage under
`/var/lib/mafia`, consistent with documentation. Development defaults may remain
relative. Incus templates will remove the ignored `MAFIA_EXECUTION_MODE` variable
and explain that execution mode is project-owned where appropriate.

Release bundling will exclude `__pycache__` directories and Python bytecode from
migrations and other copied trees.

## Compatibility

No endpoint path, response shape, database schema, persisted workflow state, or
project TOML schema changes. Existing runs continue to load. The frontend becomes
stricter about the state values it already receives.

## Testing

Each correction starts with a focused regression test where practical:

1. Stale and duplicate phase start/cancel decisions are harmless and audited.
2. Cancellation timeout leaves the original state and prevents another attempt.
3. Frontend state vocabularies match backend contracts.
4. Persisted action prompts render in decision cards.
5. Evidence failure does not hide run/activity data.
6. Dashboard decisions include `ready_for_phase`.
7. Raw TOML cannot be silently discarded and timeout bounds are enforced.
8. Project and model pages distinguish API failure from empty/not-found results.
9. PR refresh visibility and terminal polling match backend semantics.
10. Every run state maps to a stage and visual category; terminal states preserve
    the most recent meaningful stage.
11. Timestamp output is deterministic and nullable evidence ranges render cleanly.
12. Explicit settings mappings replace `.env` mappings and tests are hermetic.
13. Deployment templates use persistent data paths and release bundles omit caches.

Final verification runs the API formatter/linter, type checker, and tests; web
lint, typecheck, tests, and production build; documentation-site tests; shell
syntax checks; and the root `npm run check` command from the configured checkout.

## Finding Coverage

The design directly covers all audit findings:

1. stale phase decisions: Backend Lifecycle;
2. stop-before-transition controls: Backend Lifecycle;
3. systemd persistence path: Settings And Deployment;
4. decision prompt extraction: Decision And Refresh Flow;
5. frontend state contract drift: Frontend Contracts;
6. optional evidence failure: Decision And Refresh Flow;
7. raw TOML loss: Project Configuration Editing;
8. invalid timeout values: Project Configuration Editing;
9. phase approvals omitted from dashboard: UI Consistency And Errors;
10. project error handling: UI Consistency And Errors;
11. model-service error handling: UI Consistency And Errors;
12. no-op PR refresh: Decision And Refresh Flow;
13. `.env` mapping leakage: Settings And Deployment;
14. single-process assumption: Backend Lifecycle;
15. missing visual state mappings: UI Consistency And Errors;
16. terminal timeline reset: Decision And Refresh Flow;
17. terminal polling: Decision And Refresh Flow;
18. hydration-sensitive timestamps: UI Consistency And Errors;
19. nullable evidence range: UI Consistency And Errors; and
20. ignored Incus setting: Settings And Deployment.
