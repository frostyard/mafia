# Deployment bundle

## Build

Build releases on Linux for the same architecture as the target host:

```bash
npm run bundle
```

The command produces both `dist/mafia-<version>/` and
`dist/mafia-<version>.tar.gz`. The release contains:

- the Next.js standalone production server and static assets;
- a MAFIA Python wheel and locked runtime requirements;
- Alembic migrations and configuration;
- separate API and web launch commands.

The target host needs Python 3.11-3.13, Node.js 22+, `git`, `gh`, and the GitHub
Copilot CLI. Docker or Podman is required for Dev Container execution; rootless
`bwrap` is required for isolated fallback execution.

## Install

Extract the archive, create local configuration, and install the Python
environment:

```bash
tar -xzf mafia-0.1.0.tar.gz
cd mafia-0.1.0
cp .env.example .env
bin/install
```

`bin/install` creates a release-local `.venv` and installs the bundled MAFIA
wheel plus its locked dependencies. Re-running it updates that environment to
the release contents.

Keep `MAFIA_DATA_DIR` on persistent storage. It contains the SQLite database,
checkpoints, repository caches, analysis worktrees, and implementation
worktrees.

## Run

Start the API and web server as separate processes:

```bash
bin/api
bin/web
```

`bin/api` applies pending database migrations before starting FastAPI. By
default both services bind only to loopback:

- Next.js: `http://127.0.0.1:3000`
- FastAPI: `http://127.0.0.1:8000`

Configure the listeners with `MAFIA_WEB_HOST`, `MAFIA_WEB_PORT`,
`MAFIA_API_HOST`, and `MAFIA_API_PORT`. `MAFIA_API_URL` and `AGENT_URL` must
address the FastAPI listener. The default values are suitable when both
processes run on the same host.

The bundle intentionally keeps the processes separate so they can be managed
by systemd or another supervisor. Only the Next.js listener needs to be exposed
through the external reverse proxy.

For public deployments, configure GitHub OAuth using
`docs/authentication.md` before exposing the web listener. Authentication is
disabled by default for loopback-only development.
