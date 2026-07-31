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
- A signal-safe combined launcher and example systemd units.

## Install the bundle

```bash
tar -xzf mafia-0.1.0.tar.gz
cd mafia-0.1.0
cp .env.example .env
bin/install
```

`bin/install` creates a release-local `.venv` and installs the bundled wheel and locked dependencies.

Keep `MAFIA_DATA_DIR` on persistent storage. It contains the SQLite database, pending actions, repository caches, analysis worktrees, and implementation worktrees.

## Run the services

```bash
bin/api
bin/web
```

Use `./start.sh` when one foreground command is preferable. It forwards termination signals and stops the remaining process if either service exits.

`bin/api` applies pending migrations before starting FastAPI. Both services bind to loopback by default:

- Next.js: `http://127.0.0.1:3000`
- FastAPI: `http://127.0.0.1:8000`

Configure listeners with `MAFIA_WEB_HOST`, `MAFIA_WEB_PORT`, `MAFIA_API_HOST`, and `MAFIA_API_PORT`. `MAFIA_API_URL` must address FastAPI.

## Breaking control-plane upgrade

This upgrade intentionally discards existing runtime data. Stop services, set
`MAFIA_DATA_DIR` explicitly for the destructive and migration commands, run the
confirmed reset, run Alembic once, then restart. Do not use `bin/api` here: it
starts a blocking API process after applying migrations.

```bash
sudo systemctl stop mafia.target
sudo -u mafia env MAFIA_DATA_DIR=/var/lib/mafia /opt/mafia/current/bin/reset-data --confirm-destructive-reset
sudo -u mafia env MAFIA_DATA_DIR=/var/lib/mafia /opt/mafia/current/.venv/bin/python -m alembic -c /opt/mafia/current/alembic.ini upgrade head
sudo systemctl start mafia.target
```

`reset-data` refuses unsafe paths and any reset target containing a symlink path
component. Confirm the target before running it; it permanently removes runtime
state, caches, worktrees, and local configuration.

## Run with systemd

Example units are included under `contrib/systemd/`. They assume the active release is linked at `/opt/mafia/current`, configuration is stored at `/etc/mafia/mafia.env`, and persistent state lives at `/var/lib/mafia`.

The dedicated `mafia` service user must own its GitHub and Copilot sessions. Grant container-engine access only when Dev Container execution needs it.

Install and start the reviewed units:

```bash
sudo install -m 0644 contrib/systemd/mafia-*.service \
  contrib/systemd/mafia.target /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mafia.target
```

Keep Next.js and FastAPI on loopback and expose only the external reverse proxy. Follow [GitHub authentication](/operations/authentication/) and install the bundled `contrib/Caddyfile` before publishing a deployment.

For a private VM with persistent workflow storage and tailnet-only ingress, continue with [personal Incus deployment](/operations/incus/).
