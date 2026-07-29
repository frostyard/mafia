---
title: Frostyard organization deployment
description: Isolate organization workflows with owner policy, GitHub App credentials, and attributable operators.
group: Operations
order: 24
---

The Frostyard profile runs MAFIA in a dedicated Incus VM with separate
application state, workspaces, secrets, OAuth App, audit history, and rootless
container execution.

It separates three identities:

| Identity | Responsibility |
| --- | --- |
| Operator | Signs in through OAuth and approves decisions |
| Dedicated Copilot user | Supplies licensed model access |
| GitHub App | Reads and mutates approved Frostyard repositories |

Operator IDs and logins are attached to durable decisions, audit events,
operations, and generated pull-request context. Repository actions use
short-lived installation tokens rather than the Copilot user's credentials.

## Enforce two repository boundaries

Set `MAFIA_REPOSITORY_OWNER=frostyard` so intake, persisted runs, source
access, clone and worktree operations, GitHub calls, pushes, retries, recovery,
and merge reconciliation reject every other owner.

Create a Frostyard-owned GitHub App with read-only Metadata and Issues access,
plus read/write Contents and Pull requests access. Install it only on the
approved repositories. The selected-repository installation policy remains
effective even within the allowed owner.

Configure the App ID, installation ID, and private-key path together. Store
the key at `/etc/mafia/github-app.pem` with mode `0640`; never put it in a
profile, release, database, or environment file.

## Build the isolated VM

```bash
incus storage volume create default mafia-frostyard-data
incus profile create mafia-frostyard
incus profile edit mafia-frostyard < contrib/incus/frostyard.yaml
incus launch images:ubuntu/24.04/cloud mafia-frostyard \
  --vm \
  --profile default \
  --profile mafia-frostyard
incus exec mafia-frostyard -- cloud-init status --wait
```

Copy `contrib/incus/frostyard.env.example` to `/etc/mafia/mafia.env`.
Use a dedicated OAuth App, independent session and internal secrets, and
`MAFIA_GITHUB_ALLOWED_ORG=frostyard`.

Authenticate the Copilot CLI as a dedicated licensed GitHub user. That user
does not need repository write access because host Git and GitHub operations
use the App installation.

Keep Caddy as the only listener, permit HTTPS only over `tailscale0`, and do
not add an Incus proxy device or public port forward.

See the repository's [complete Frostyard deployment guide](https://github.com/frostyard/mafia/blob/main/docs/frostyard-incus.md) for App setup, secret installation, release deployment, readiness, backup, restore, and rotation procedures.
