# Development

## Prerequisites

- Python 3.11-3.13 and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and npm
- `git`, `gh`, GitHub Copilot CLI, and rootless `bwrap`
- Docker or Podman 5+ for repositories that provide a Dev Container
- Signed-in GitHub CLI and GitHub Copilot CLI sessions

## Local setup

```bash
cp .env.example .env
uv sync
npm install
npm install --prefix apps/web
uv run alembic upgrade head
```

Run the API:

```bash
uv run uvicorn mafia.main:app --reload
```

Run the web application in a separate terminal:

```bash
npm run dev
```

The API listens on `http://127.0.0.1:8000`; the web application listens on
`http://127.0.0.1:3000`.

The root `npm run dev` command loads the repository `.env` into Next.js.
Authentication remains disabled unless `MAFIA_AUTH_MODE=github` is explicitly
configured; see [GitHub web authentication](authentication.md).

Runtime state is stored under `data/` and intentionally excluded from Git. It includes SQLite state,
checkpoints, repository caches, analysis worktrees, and phase worktrees.

## Validation

Run the complete local gate:

```bash
npm run check
```

The individual gates are also available:

```bash
npm run check:api
npm run check:web
npm run check:scripts
```
