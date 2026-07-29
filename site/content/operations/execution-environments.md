---
title: Execution environments
description: Choose isolated containers, bubblewrap, or explicitly trusted host execution.
group: Operations
order: 20
---

`MAFIA_EXECUTION_MODE=isolated` is the default. It selects a Dev Container when the repository provides one and otherwise uses bubblewrap.

Set `MAFIA_EXECUTION_MODE=host` only for a trusted local deployment that should run implementation and validation commands directly on the host from the phase worktree.

## Isolated mode

mafia prefers a repository's Dev Container for implementation and validation. Repositories without `.devcontainer/devcontainer.json` or `.devcontainer.json` use the network-isolated rootless bubblewrap sandbox.

`MAFIA_CONTAINER_ENGINE=auto` prefers Docker and falls back to Podman. Set it to `docker` or `podman` to require a specific engine.

Dev Container lifecycle and setup commands initially run with network access. The default `MAFIA_DEVCONTAINER_NETWORK=setup-only` disconnects every container network before model-directed implementation begins.

## Configuration policy

`MAFIA_DEVCONTAINER_POLICY` has two modes:

- `strict` rejects host commands, privileged execution, Compose, host or named mounts, local environment substitution, added capabilities, custom security options, host namespaces, devices, GPU access, and published ports.
- `allow-anything` permits native Dev Container features for repositories that the operator explicitly trusts.

Both modes require the Dev Container configuration to be a regular file inside the checked-out worktree.

## Host mode

Host mode skips Dev Container discovery, container-engine setup, bubblewrap, network isolation, and process isolation. File tools remain confined to the worktree, and command timeouts, output limits, cancellation, diff validation, and host-owned Git operations still apply.

Shell commands can nevertheless access the host filesystem, network, and programs. Do not use host mode with untrusted requirements or repositories.

## Host-owned boundaries

The implementation agent runs commands through the selected environment. Git fetches, worktree creation, final diff verification, commits, pushes, pull request creation, and merge reconciliation remain host-owned operations.

GitHub and Copilot credentials are not mounted into isolated execution environments.
