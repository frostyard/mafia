# Project configuration

mafia uses `.mafia.toml` for deterministic validation and host-owned project
execution settings. Open **Projects** in the web interface to add a repository,
choose its execution mode, manage validation commands, or import and export the
host TOML.

## Repository configuration

Commit `.mafia.toml` at the repository root when you can manage the repository:

```toml
version = 1

[[validation.commands]]
name = "Repository checks"
run = "npm run check"
working_directory = "."
timeout_seconds = 1800
```

Commands run sequentially from the selected execution environment and every
command must exit successfully. `working_directory` is relative to the
repository root and cannot escape it. Timeouts must be between 1 and 3600
seconds.

Repository configuration cannot contain `[execution]`. Repository content is
untrusted and can never opt mafia into host execution.

Use the `contrib/skills/setup-mafia` skill to have an agent inspect contributor
guidance, manifests, task runners, and CI before creating the file. The chosen
commands should represent the repository's existing complete gate rather than
a new or weaker proxy.

## Host project configuration

When a repository cannot accept `.mafia.toml`, configure the project in the web
interface. mafia writes the host copy to:

```text
data/projects/<owner>/<repository>/.mafia.toml
```

The host file uses the same validation format and may additionally select the
execution mode:

```toml
version = 1

[execution]
mode = "isolated"

[[validation.commands]]
name = "Repository checks"
run = "npm run check"
working_directory = "."
timeout_seconds = 1800
```

Repository validation wins when the source commit contains `.mafia.toml`. The
host validation is used only when the repository file is absent. A repository
file without validation does not silently fall back to the host commands.
Execution mode always comes from the host file.

## Workflow behavior

New projects default to isolated execution. Specification and planning can
proceed without validation, but mafia does not offer phase approval until
validation resolves from the phase's immutable source commit or the host
fallback.

At phase execution, mafia freezes the resolved configuration and runs its
commands before implementation review. If remediation changes the candidate,
mafia runs the same frozen commands again. Agent-reported targeted commands are
supplemental and cannot replace this gate.

Pull request reviews resolve repository configuration from the immutable base
commit, so a pull request cannot introduce commands that mafia then executes.
When validation is configured, mafia runs it against the exact head before model review, records the
results, and restores the analysis worktree to the exact head. Reviews may
continue with an explicit `not_configured` result when neither source exists.
