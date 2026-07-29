# Personal Incus deployment

The personal profile runs mafia in an Incus virtual machine with its own
operating system, rootless container engine, application state, credentials,
and audit history. It does not restrict repository owners: GitHub operations
have the same reach as the GitHub credentials owned by the `mafia` service
user.

## Create the persistent volume and profile

Review storage pool names and resource limits in
`contrib/incus/personal.yaml`, then create the custom data volume and profile:

```bash
incus storage volume create default mafia-personal-data
incus profile create mafia-personal
incus profile edit mafia-personal < contrib/incus/personal.yaml
```

The custom volume is mounted at `/var/lib/mafia`. It persists the service
user's home, SQLite state, checkpoints, repository caches, analysis worktrees,
implementation worktrees, and GitHub and Copilot sessions independently of
the VM root disk.

## Launch the VM

Use a cloud image so the profile's `cloud-init.vendor-data` creates the
service user and installs the base isolation tools:

```bash
incus launch images:ubuntu/24.04/cloud mafia-personal \
  --vm \
  --profile default \
  --profile mafia-personal
incus exec mafia-personal -- cloud-init status --wait
incus config show mafia-personal --expanded
```

The VM receives networking and its root disk from the existing `default`
profile. The `mafia-personal` profile adds CPU and memory limits, autostart,
cloud-init provisioning, and the persistent data volume.

Install Node.js 22+, GitHub CLI, GitHub Copilot CLI, Caddy, Tailscale, and the
Dev Container CLI inside the VM using their official installation methods.
Verify rootless Podman as the service user:

```bash
incus exec mafia-personal -- sudo -iu mafia podman info
```

Container execution stays inside the VM. Do not pass the Incus host's Docker,
Podman, Incus, or filesystem sockets into the guest.

## Install mafia

Build the release for the VM architecture and copy it into the guest:

```bash
npm run bundle
incus file push dist/mafia-0.1.0.tar.gz \
  mafia-personal/var/tmp/mafia-0.1.0.tar.gz
incus exec mafia-personal -- sudo -iu mafia -- \
  tar -C /opt/mafia -xzf /var/tmp/mafia-0.1.0.tar.gz
incus exec mafia-personal -- ln -sfn \
  /opt/mafia/mafia-0.1.0 /opt/mafia/current
incus exec mafia-personal -- sudo -iu mafia -- \
  /opt/mafia/current/bin/install
```

Copy `contrib/incus/personal.env.example` to
`/etc/mafia/mafia.env`, replace every placeholder, and set mode `0640` with
group `mafia`. Generate independent session and internal secrets with
`openssl rand -hex 32`.

Create a dedicated GitHub OAuth App for this VM. Configure only the owner's
immutable GitHub user ID unless another explicit personal access policy is
required. Do not configure `MAFIA_GITHUB_ALLOWED_ORG` merely to broaden access.

Authenticate GitHub and Copilot as the service user:

```bash
incus exec mafia-personal -- sudo -iu mafia gh auth login
incus exec mafia-personal -- sudo -iu mafia copilot
```

Use the sign-in command supported by the installed Copilot CLI if it differs.
Those credentials can act on every repository they are authorized to access.

## Keep ingress on the tailnet

Join Tailscale from inside the VM without placing reusable auth keys in the
profile. Use the VM's tailnet DNS name for `MAFIA_GITHUB_OAUTH_CALLBACK_URL`
and `MAFIA_DOMAIN`.

Do not add an Incus proxy device or public port forward. Configure the guest
firewall to accept HTTPS only on `tailscale0`, and keep Next.js and FastAPI on
loopback:

```bash
incus exec mafia-personal -- ufw default deny incoming
incus exec mafia-personal -- ufw allow in on tailscale0 to any port 443 proto tcp
incus exec mafia-personal -- ufw --force enable
```

Install `contrib/Caddyfile` in the VM and follow Tailscale's HTTPS certificate
guidance for the tailnet hostname. GitHub OAuth does not require GitHub to
connect inbound to the callback; the signed-in browser follows that URL over
the tailnet.

## Start and update

Install the example systemd units and enable `mafia.target` as described in
`docs/deployment.md`. For updates:

1. Build and copy a new release.
2. Run its `bin/install`.
3. atomically repoint `/opt/mafia/current`.
4. restart `mafia.target`.
5. retain the previous release until the new version is healthy.

The custom data volume is not replaced during release updates. Snapshot or
back up `mafia-personal-data` before migrations and periodically test restore
procedures.
