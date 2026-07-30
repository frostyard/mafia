---
title: Project configuration
description: Configure deterministic validation and per-project execution.
group: Reference
order: 20
---

Open **Projects** in the mafia web interface to add a repository, choose its execution mode, manage validation commands, or import and export its host `.mafia.toml`.

## Repository `.mafia.toml`

Commit this file at the repository root when you can manage the repository:

```toml
version = 1

[[validation.commands]]
name = "Repository checks"
run = "npm run check"
working_directory = "."
timeout_seconds = 1800
```

Commands run sequentially and every command must pass. Working directories are repository-relative. Timeouts must be between 1 and 3600 seconds.

Repository configuration cannot contain `[execution]`. Untrusted repository content can never select host execution.

The `contrib/skills/setup-mafia` skill instructs an agent to inspect contributor guidance, manifests, task runners, and CI before choosing the repository's existing complete validation gate.

## Host fallback

For repositories where you cannot commit a dotfile, mafia stores a host copy at:

```text
data/projects/<owner>/<repository>/.mafia.toml
```

The host file may additionally select execution:

```toml
version = 1

[execution]
mode = "isolated"
```

Repository validation wins when `.mafia.toml` exists at the immutable source commit. Host validation is fallback only. Execution mode always comes from host-owned settings.

## Gates

Planning may proceed without validation, but phase approval remains unavailable until validation is configured. mafia freezes the resolved commands for the phase, runs them before implementation review, and runs them again after remediation.

Pull request review resolves repository validation from the immutable base commit, so the pull request cannot introduce commands that mafia then executes. It runs those commands against the exact head before model review and includes the result in the consolidated artifact. If validation is absent, the review records `not_configured` and continues.
