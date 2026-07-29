---
title: Deployment
description: Build, install, and run the two-process release bundle.
group: Operations
order: 21
---

## Build a release

Build releases on Linux for the same architecture as the target host:

```bash
npm run bundle
```

The command produces `dist/mafia-<version>/` and `dist/mafia-<version>.tar.gz`.

The release contains:

- The Next.js standalone production server and static assets.
- A mafia Python wheel and locked runtime requirements.
- Alembic migrations and configuration.
- Separate API and web launch commands.

## Install the bundle

```bash
tar -xzf mafia-0.1.0.tar.gz
cd mafia-0.1.0
cp .env.example .env
bin/install
```

`bin/install` creates a release-local `.venv` and installs the bundled wheel and locked dependencies.

Keep `MAFIA_DATA_DIR` on persistent storage. It contains the SQLite database, checkpoints, repository caches, analysis worktrees, and implementation worktrees.

## Run the services

```bash
bin/api
bin/web
```

`bin/api` applies pending migrations before starting FastAPI. Both services bind to loopback by default:

- Next.js: `http://127.0.0.1:3000`
- FastAPI: `http://127.0.0.1:8000`

Configure listeners with `MAFIA_WEB_HOST`, `MAFIA_WEB_PORT`, `MAFIA_API_HOST`, and `MAFIA_API_PORT`. `MAFIA_API_URL` and `AGENT_URL` must address FastAPI.

Only expose Next.js through the external reverse proxy. Follow [GitHub authentication](/operations/authentication/) before publishing a deployment.
