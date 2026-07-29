---
title: Installation
description: Install mafia from source and start both local services.
group: Getting started
order: 2
---

## Prerequisites

- Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and npm
- `git`, `gh`, GitHub Copilot CLI, and rootless `bwrap`
- Docker or Podman 5+ when repositories provide Dev Containers
- Signed-in GitHub CLI and GitHub Copilot CLI sessions

## Install from source

```bash
git clone https://github.com/frostyard/mafia.git
cd mafia
cp .env.example .env
uv sync
npm install
npm install --prefix apps/web
npm install --prefix site
uv run alembic upgrade head
```

## Start the services

Start FastAPI:

```bash
uv run uvicorn mafia.main:app --reload
```

In another terminal, start Next.js:

```bash
npm run dev
```

Open `http://127.0.0.1:3000`. Authentication is disabled by default for loopback-only development.

## Verify the checkout

```bash
npm run check
```

The complete gate validates the API, web application, documentation site, and release scripts.
