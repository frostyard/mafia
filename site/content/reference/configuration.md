---
title: Configuration
description: Runtime settings for models, listeners, execution, authentication, and persistence.
group: Reference
order: 30
---

mafia reads `.env` through the `MAFIA_` prefix. Start from `.env.example`.

## Core settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAFIA_DATA_DIR` | `./data` | Database, checkpoints, repository caches, and worktrees |
| `MAFIA_API_HOST` | `127.0.0.1` | FastAPI bind address |
| `MAFIA_API_PORT` | `8000` | FastAPI port |
| `MAFIA_WEB_HOST` | `127.0.0.1` | Next.js bind address in the release bundle |
| `MAFIA_WEB_PORT` | `3000` | Next.js port in the release bundle |
| `MAFIA_API_URL` | `http://127.0.0.1:8000` | FastAPI URL used by Next.js |
| `MAFIA_MODEL_PAIRS` | reciprocal Opus and GPT pair | Primary-to-reviewer model mapping |
| `MAFIA_REPOSITORY_OWNER` | unset | Restrict every repository operation to one GitHub owner |

## Execution settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAFIA_CONTAINER_ENGINE` | `auto` | Select Docker, Podman, or automatic detection |
| `MAFIA_DEVCONTAINER_POLICY` | `strict` | Select strict validation or trusted `allow-anything` |
| `MAFIA_DEVCONTAINER_NETWORK` | `setup-only` | Disconnect networking before model-directed work |
| `MAFIA_CONTAINER_CPU_LIMIT` | `4` | Container CPU limit |
| `MAFIA_CONTAINER_MEMORY_LIMIT` | `4g` | Container memory limit |
| `MAFIA_SANDBOX_PROCESS_LIMIT` | `128` | Process ceiling for isolated execution |

Execution mode is configured per project in the web interface rather than through an environment variable. See [Project configuration](/reference/project-configuration/).

## Authentication settings

Set `MAFIA_AUTH_MODE=github` and configure OAuth credentials, `MAFIA_GITHUB_SESSION_SECRET`, and `MAFIA_INTERNAL_SECRET`.

At least one of these authorization policies is mandatory:

- `MAFIA_GITHUB_ALLOWED_USER_IDS`
- `MAFIA_GITHUB_ALLOWED_ORG`

Read [GitHub authentication](/operations/authentication/) before exposing the web listener.

## GitHub App repository identity

Organization deployments can separate repository actions from the licensed
Copilot user:

| Variable | Purpose |
| --- | --- |
| `MAFIA_GITHUB_APP_ID` | GitHub App identifier |
| `MAFIA_GITHUB_APP_INSTALLATION_ID` | Selected-repository installation identifier |
| `MAFIA_GITHUB_APP_PRIVATE_KEY_PATH` | Path to the App's PEM private key |
| `MAFIA_REPOSITORY_ACTION_NAME` | Git commit author name |
| `MAFIA_REPOSITORY_ACTION_EMAIL` | Git commit author email |

Configure all three App credential values together. MAFIA mints short-lived
installation tokens for host `git` and `gh` commands without storing tokens
in SQLite. See [Frostyard organization deployment](/operations/frostyard-incus/).
