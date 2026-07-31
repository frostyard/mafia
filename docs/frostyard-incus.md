# Frostyard organization deployment

The Frostyard profile runs in a dedicated Incus virtual machine with its own
database, workspaces, repository cache, secrets, OAuth App, audit history, and
rootless container runtime. It accepts only `frostyard/*` repositories and
uses three separate identities:

| Identity | Purpose | Credential |
| --- | --- | --- |
| Operator | Signs in to the web application and approves work | GitHub OAuth session |
| Copilot user | Supplies licensed model access | Dedicated GitHub user with a Copilot seat |
| Repository actor | Reads and mutates approved repositories | Short-lived GitHub App installation token |

Operator IDs and logins are recorded on decisions, audit events, operations,
and generated pull-request context. The Copilot account does not supply
repository credentials.

## Create the volume and VM

Review the storage pool and limits in `contrib/incus/frostyard.yaml`, then
create a volume and profile that are distinct from every personal deployment:

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

The volume mounted at `/var/lib/mafia` persists the service user's home and all
application state independently of the VM root disk. Never share this volume,
an environment file, OAuth App, or GitHub App private key with a personal
deployment.

Install Node.js 22+, GitHub CLI, GitHub Copilot CLI, Caddy, Tailscale, and the
Dev Container CLI using their official installation methods. Verify rootless
Podman as the service user:

```bash
incus exec mafia-frostyard -- sudo -iu mafia podman info
```

Do not pass host container-engine, Incus, or filesystem sockets into the VM.

## Create the GitHub App

Create a GitHub App owned by the Frostyard organization with no webhook URL
unless a later deployment explicitly enables webhooks. Grant only these
repository permissions:

- **Metadata:** read-only, as required by GitHub.
- **Contents:** read and write for clone, branch, commit, and push operations.
- **Issues:** read-only for source requirements and comments.
- **Pull requests:** read and write for lookup, creation, review comments, and
  merge reconciliation.

Install the App only on the approved Frostyard repositories that MAFIA may
change, not on all current and future repositories. Record the App ID and
installation ID. Generate a private key, copy it directly into the guest, and
restrict it to root and the `mafia` group:

```bash
incus file push github-app.private-key.pem \
  mafia-frostyard/etc/mafia/github-app.pem
incus exec mafia-frostyard -- chown root:mafia /etc/mafia/github-app.pem
incus exec mafia-frostyard -- chmod 0640 /etc/mafia/github-app.pem
```

Delete any unnecessary local copy after confirming the deployment can mint an
installation token. Never place the key in the Incus profile, release archive,
environment file, application database, or backup logs.

`MAFIA_REPOSITORY_OWNER=frostyard` is a second enforcement layer in addition
to the installation's selected-repository policy. MAFIA checks it during
intake, persisted-run access, source metadata lookup, clone and worktree
operations, GitHub API calls, push, retry, recovery, and merge reconciliation.
It also rejects a GitHub metadata response whose clone URL names another
repository.

## Configure OAuth and Copilot

Create an OAuth App dedicated to this VM. Set its callback to the exact
tailnet HTTPS URL and configure `MAFIA_GITHUB_ALLOWED_ORG=frostyard`.
Organization authorization requests only `read:org`; it does not grant
repository access.

Provision a dedicated GitHub user with a Copilot license and authenticate the
Copilot CLI as the `mafia` service user:

```bash
incus exec mafia-frostyard -- sudo -iu mafia copilot
```

Use the sign-in command supported by the installed Copilot CLI. Do not grant
this user repository write access merely to operate MAFIA. Every host `git`
and `gh` repository command receives a short-lived installation token and a
command-scoped Git credential helper instead.

Copy `contrib/incus/frostyard.env.example` to `/etc/mafia/mafia.env`. Fill in
the OAuth and GitHub App identifiers, and generate independent session and
internal secrets:

```bash
openssl rand -hex 32
openssl rand -hex 32
incus exec mafia-frostyard -- chown root:mafia /etc/mafia/mafia.env
incus exec mafia-frostyard -- chmod 0640 /etc/mafia/mafia.env
```

Do not configure `MAFIA_GITHUB_ALLOWED_USER_IDS` unless specific operators
must bypass the organization-membership check.

## Install and expose the service

Build and install the release as described in `docs/incus.md`, using the
`mafia-frostyard` instance name. Install the systemd units from
`contrib/systemd/`, enable `mafia.target`, and verify `/readyz` reports GitHub
App installation authentication as available.

Join Tailscale from inside the VM. Do not add an Incus proxy device or public
port forward. Keep FastAPI and Next.js on loopback, and allow HTTPS only on
`tailscale0`:

```bash
incus exec mafia-frostyard -- ufw default deny incoming
incus exec mafia-frostyard -- \
  ufw allow in on tailscale0 to any port 443 proto tcp
incus exec mafia-frostyard -- ufw --force enable
```

Install `contrib/Caddyfile` and configure its domain for the tailnet hostname.
Caddy authenticates every application route before it reaches Next.js or
FastAPI.

## Update, backup, and rotate

Before an update, back up the data volume and retain the previous release.
Install the new release, run `bin/install`, atomically repoint
`/opt/mafia/current`, restart `mafia.target`, and check readiness.

Back up the custom volume and `/etc/mafia` separately with encrypted,
access-controlled storage. The volume contains source excerpts and audit
history; `/etc/mafia` contains live secrets. Test restoration into an isolated
VM without connecting it to production repositories.

Rotate the OAuth client secret, cookie secret, internal secret, and GitHub App
private key independently. Revoke the old App key after readiness succeeds
with the replacement. Review the App's selected repositories and permissions
regularly, and remove access that is no longer required.
