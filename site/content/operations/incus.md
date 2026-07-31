---
title: Personal Incus deployment
description: Run a private personal deployment in an isolated Incus virtual machine.
group: Operations
order: 23
---

The personal profile runs mafia in an Incus virtual machine with isolated operating-system state, credentials, runtime data, and rootless container execution.

It does not restrict repository owners. GitHub mutations have the same reach as the credentials owned by the VM's `mafia` service user.

## Create the profile

Review storage pool names and resource limits in `contrib/incus/personal.yaml`:

```bash
incus storage volume create default mafia-personal-data
incus profile create mafia-personal
incus profile edit mafia-personal < contrib/incus/personal.yaml
```

The custom volume is mounted at `/var/lib/mafia`. It persists the service user's home, workflow database, pending actions, repository caches, worktrees, and authenticated CLI sessions.

## Launch the VM

```bash
incus launch images:ubuntu/24.04/cloud mafia-personal \
  --vm \
  --profile default \
  --profile mafia-personal
incus exec mafia-personal -- cloud-init status --wait
```

Install Node.js 22+, GitHub CLI, GitHub Copilot CLI, Caddy, Tailscale, and the Dev Container CLI inside the VM. Rootless Podman and bubblewrap are installed by cloud-init.

Never pass host container-engine, Incus, or filesystem sockets into the guest.

## Configure personal access

Copy `contrib/incus/personal.env.example` to `/etc/mafia/mafia.env`, replace every placeholder, and generate independent cookie and internal secrets.

Create a dedicated OAuth App and authorize the owner's immutable GitHub user ID. Authenticate GitHub and Copilot as the `mafia` service user. Those credentials can act on any repository they are permitted to access.

## Keep ingress private

Join Tailscale from inside the VM without storing a reusable auth key in the profile. Use the VM's tailnet hostname for the OAuth callback and Caddy domain.

Do not add an Incus proxy device or public port forward. Permit HTTPS only on `tailscale0`; keep Next.js and FastAPI bound to loopback.

See the repository's [complete Incus guide](https://github.com/frostyard/mafia/blob/main/docs/incus.md) for release installation, firewall, systemd, update, backup, restore, and destructive-upgrade procedures.
