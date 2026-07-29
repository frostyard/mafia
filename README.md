# MAFIA

[![CI](https://github.com/frostyard/mafia/actions/workflows/ci.yml/badge.svg)](https://github.com/frostyard/mafia/actions/workflows/ci.yml)

MAFIA is a source-grounded engineering workflow for specification-driven delivery and ad-hoc pull request
review. It can turn a GitHub issue or written requirement into an accepted specification, adversarially
reviewed implementation plan, and merged pull requests, or independently review an existing pull request
with a configurable model pair before adjudicating one publishable result.

## Quick start

Prerequisites are Python 3.11-3.13 with [uv](https://docs.astral.sh/uv/), Node.js 22+, npm, `git`, `gh`,
GitHub Copilot CLI, and rootless `bwrap`. Docker or Podman is required to use repository Dev Containers.

```bash
cp .env.example .env
uv sync
npm install
npm install --prefix apps/web
npm install --prefix site
uv run alembic upgrade head
uv run uvicorn mafia.main:app --reload
npm run dev
```

Open `http://127.0.0.1:3000`, create a run, and start the workflow.

## Documentation

- [Documentation site](https://mafia.frostyard.org) — guided setup, workflow, operations, and reference.
- [Workflow lifecycle](docs/workflow.md) — artifacts, approvals, pull requests, durability, and recovery.
- [Execution environments](docs/execution-environments.md) — Dev Containers, engines, networking, policy,
  resource limits, and bubblewrap fallback.
- [Development](docs/development.md) — prerequisites, local services, runtime data, and validation commands.
- [Deployment bundle](docs/deployment.md) — release packaging, installation, configuration, and production
  processes.
- [GitHub authentication](docs/authentication.md) — OAuth App setup, user or organization authorization,
  secure sessions, and Caddy integration.
- [Personal Incus deployment](docs/incus.md) — persistent VM profile, private tailnet ingress, and updates.

## License

MAFIA is available under the [MIT License](LICENSE).
