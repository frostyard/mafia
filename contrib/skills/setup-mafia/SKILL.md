---
name: setup-mafia
description: Inspect a repository and create its deterministic mafia validation configuration.
---

# Set up mafia validation

Create a repository-owned `.mafia.toml` that gives mafia a deterministic,
mechanical validation gate.

## Inspect before editing

1. Read the repository instructions and contributor documentation.
2. Inspect package manifests, lockfiles, Makefiles, task runners, and existing CI
   workflows.
3. Identify the canonical command that contributors and CI use to run all
   required tests, linting, type checks, builds, generated-file checks, and
   documentation checks.
4. Prefer an existing aggregate command such as `npm run check`, `make check`,
   `just check`, or the repository's documented equivalent. Do not invent a
   weaker proxy when CI already defines the real gate.
5. Confirm the selected command works from a clean checkout with the
   repository's documented setup.

## Create `.mafia.toml`

Repository configuration must contain validation only. Never add an
`[execution]` section; execution mode is a host-owned mafia project setting.

```toml
version = 1

[[validation.commands]]
name = "Repository checks"
run = "npm run check"
working_directory = "."
timeout_seconds = 1800
```

Use multiple commands only when the repository has no canonical aggregate
command. Commands run sequentially and every command must pass. A working
directory must be repository-relative and cannot contain `..`.

Choose a timeout that accommodates a normal uncached run, up to 3600 seconds.
Do not use no-op commands, commands that suppress failures, or conditional
fallbacks that turn a failed check into success.

## Verify

Run every configured command exactly as written from its configured working
directory. Leave dependency installation and environment provisioning to the
repository's established setup rather than embedding ad hoc installation in
the validation command.
