---
title: Development
description: Run the source checkout, validate changes, and understand local state.
group: Reference
order: 31
---

## Local services

Start FastAPI:

```bash
uv run uvicorn mafia.main:app --reload
```

Start Next.js in another terminal:

```bash
npm run dev
```

The root command loads the repository `.env` into Next.js.

Start the documentation site when changing guides or its interface:

```bash
npm run dev:site
```

## Runtime state

Runtime state lives under `data/` and is excluded from Git:

- SQLite workflow and operation state
- SQLite pending actions
- Repository caches
- Analysis worktrees
- Phase implementation worktrees

## Validation

Run the complete local gate:

```bash
npm run check
```

Run individual gates when narrowing feedback:

```bash
npm run check:api
npm run check:web
npm run check:site
npm run check:scripts
```

The same commands back GitHub Actions.
