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

## Execution settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAFIA_EXECUTION_MODE` | `isolated` | Select `isolated` or trusted `host` execution |
| `MAFIA_CONTAINER_ENGINE` | `auto` | Select Docker, Podman, or automatic detection |
| `MAFIA_DEVCONTAINER_POLICY` | `strict` | Select strict validation or trusted `allow-anything` |
| `MAFIA_DEVCONTAINER_NETWORK` | `setup-only` | Disconnect networking before model-directed work |
| `MAFIA_CONTAINER_CPU_LIMIT` | `4` | Container CPU limit |
| `MAFIA_CONTAINER_MEMORY_LIMIT` | `4g` | Container memory limit |
| `MAFIA_SANDBOX_PROCESS_LIMIT` | `128` | Process ceiling for isolated execution |

## Authentication settings

Set `MAFIA_AUTH_MODE=github` and configure OAuth credentials, `MAFIA_GITHUB_SESSION_SECRET`, and `MAFIA_INTERNAL_SECRET`.

At least one of these authorization policies is mandatory:

- `MAFIA_GITHUB_ALLOWED_USER_IDS`
- `MAFIA_GITHUB_ALLOWED_ORG`

Read [GitHub authentication](/operations/authentication/) before exposing the web listener.
