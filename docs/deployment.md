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
- separate API and web launch commands;
- a signal-safe combined launcher and example systemd units.

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
repository caches, analysis worktrees, and implementation worktrees.

Execution mode is configured per project in the host project configuration;
deployment environment files do not select it. See `docs/project-configuration.md`.

## Run

Start the API and web server as separate processes:

```bash
bin/api
bin/web
```

For a foreground process manager or a temporary installation, `start.sh` starts
both services. It forwards `SIGINT` and `SIGTERM`, stops the remaining process
if either service exits, and returns the first service's exit status:

```bash
./start.sh
```

`bin/api` applies pending database migrations before starting FastAPI. By
default both services bind only to loopback:

- Next.js: `http://127.0.0.1:3000`
- FastAPI: `http://127.0.0.1:8000`

Configure the listeners with `MAFIA_WEB_HOST`, `MAFIA_WEB_PORT`,
`MAFIA_API_HOST`, and `MAFIA_API_PORT`. `MAFIA_API_URL` must address the
FastAPI listener. The default values are suitable when both processes run on
the same host.

MAFIA supports exactly one API worker (`MAFIA_API_WORKERS=1`). Do not launch
Uvicorn or Gunicorn with multiple workers: active-work cancellation is
process-local, so multiple API processes can publish conflicting run state. Run
only one API service instance for a deployment; horizontal API scaling is not
supported.

## Breaking control-plane upgrade

This release intentionally discards all existing runtime data. Stop both
services, set the data directory explicitly for each destructive or migration
command, reset it with its required confirmation, run Alembic once, and only
then restart the services. Do not use `bin/api` for the migration because it
starts a blocking API process after applying migrations.

```bash
sudo systemctl stop mafia.target
sudo -u mafia env MAFIA_DATA_DIR=/var/lib/mafia /opt/mafia/bin/reset-data --confirm-destructive-reset
sudo -u mafia env MAFIA_DATA_DIR=/var/lib/mafia /opt/mafia/.venv/bin/python -m alembic -c /opt/mafia/alembic.ini upgrade head
sudo systemctl start mafia.target
```

`reset-data` refuses unsafe targets and any target whose path contains a
symlink component. Verify the intended persistent directory before confirming:
the reset permanently removes its database, caches, worktrees, and local
configuration.

## systemd

The bundle includes example units under `contrib/systemd/`. They assume:

- releases are installed under `/opt/mafia/` and `/opt/mafia/current` points to
  the active release;
- a dedicated `mafia` user and group own runtime work;
- deployment configuration is stored at `/etc/mafia/mafia.env`;
- `MAFIA_DATA_DIR` defaults to `/var/lib/mafia`, and the `mafia` user's GitHub,
  Copilot, and container-engine credentials live there.

`/etc/mafia/mafia.env` can override that default. systemd applies the unit's
`EnvironmentFile=` values after its `Environment=` values, so set
`MAFIA_DATA_DIR` there only when the operator intentionally uses another
persistent location.

Install the examples after reviewing their paths and hardening for the target
host:

```bash
sudo install -d -m 0750 -o root -g mafia /etc/mafia
sudo install -m 0640 -o root -g mafia .env /etc/mafia/mafia.env
sudo install -m 0644 contrib/systemd/mafia-*.service \
  contrib/systemd/mafia.target /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mafia.target
```

Authenticate host tools as the service user before starting the units:

```bash
sudo -u mafia -H gh auth login
sudo -u mafia -H copilot
```

Use the sign-in command supported by the installed Copilot CLI if it differs.
If Dev Containers use Docker or Podman, grant the `mafia` user only the runtime
access required by that host.

The bundle intentionally keeps API and web supervision separate. Only the
reverse proxy listens publicly; both application listeners remain on loopback.

For public deployments, configure GitHub OAuth using
`docs/authentication.md` and install the example `contrib/Caddyfile`.
Authentication is disabled by default for loopback-only development.

For a private personal VM, follow `docs/incus.md` and start from the bundled
`contrib/incus/personal.yaml` profile.
