# Execution environments

`MAFIA_EXECUTION_MODE=isolated` is the default. It selects a Dev Container when the repository provides
one and otherwise uses bubblewrap.

Set `MAFIA_EXECUTION_MODE=host` for a trusted local deployment that should run implementation and
validation commands directly on the host from the phase worktree. Host mode skips Dev Container discovery,
container-engine setup, bubblewrap, network isolation, and process isolation. File tools remain confined to
the worktree, and command timeouts, output limits, cancellation, diff validation, and host-owned Git/GitHub
operations still apply. Shell commands can nevertheless access the host filesystem, network, and programs;
do not use host mode with untrusted requirements or repositories.

## Isolated mode

MAFIA prefers a repository's Dev Container for implementation and validation. Repositories without
`.devcontainer/devcontainer.json` or `.devcontainer.json` use the network-isolated rootless bubblewrap
sandbox.

## Container engine

`MAFIA_CONTAINER_ENGINE=auto` prefers Docker and falls back to Podman. Set it to `docker` or `podman` to
require a specific engine.

Dev Container lifecycle and setup commands initially run with network access. The default
`MAFIA_DEVCONTAINER_NETWORK=setup-only` disconnects every container network before model-directed
implementation begins. Set it to `enabled` only when implementation or validation explicitly requires
network access.

MAFIA applies configured CPU, memory, swap, and process limits after the container starts. It removes the
container and anonymous volumes after success, failure, timeout, or cancellation.

## Configuration policy

`MAFIA_DEVCONTAINER_POLICY` has two modes:

- `strict` is the default. It rejects host commands, privileged execution, Compose, host, bind, or named
  mounts, local environment substitution, added capabilities, custom security options, host namespaces,
  devices, GPU access, and port publication. Raw configuration is checked before Dev Container expansion
  so host values cannot leak through `${localEnv:...}` or `${env:...}`.
- `allow-anything` bypasses configuration-content restrictions for trusted repositories. This permits
  capabilities, security options, mounts, host commands, local environment substitution, and other native
  Dev Container features.

Both modes require the Dev Container configuration to be a regular file inside the checked-out worktree.
The allow-anything mode trusts repository configuration with the host access provided by the selected
container engine; do not enable it for untrusted repositories.

## Host-owned boundaries

The implementation agent runs commands through the selected execution environment. Git fetches, worktree
creation, final diff verification, commits, pushes, pull-request creation, and merge reconciliation remain
host-owned operations. GitHub and Copilot credentials are not mounted into the execution environment.
