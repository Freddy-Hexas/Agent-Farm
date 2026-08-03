# Agent Farm Execution Sandbox

Agent Farm treats model-generated file, network, and process actions as untrusted.

## Execution backends

- `auto` is the secure default. Commands run in Docker with no network, no Linux capabilities, a read-only container root, a bounded process count, and explicit CPU, memory, runtime, and output limits.
- `docker` always requires the Docker backend.
- `windows` is limited to tightly validated read-only host commands. It uses a Windows Job Object, a sanitized environment, bounded resources, and process-tree termination. It refuses repository code and refuses host execution when forbidden-path rules are active.
- `danger-full-access` is an explicit opt-out. It may run repository code on the host and should not be used for untrusted repositories.

The Docker runner copies the worktree into a temporary directory before mounting it. Git metadata, Agent Farm runtime data, build caches, forbidden paths, and symlinks are excluded. Repository scripts therefore cannot access the original worktree or host files through the mount.

Docker images are never pulled implicitly. If a required image is absent or Docker Desktop is stopped, execution fails closed with a capability manifest and return code `126`.

## Network policy

Repository commands always run with `--network=none`. Native web tools are disabled unless network access is configured. With an approval policy of `on-request` or `untrusted`, each search or public host must be approved before use. Localhost, private networks, link-local addresses, credential-bearing URLs, and non-HTTP schemes are blocked.

## Audit records

Every native tool call emits a `tool.capability` event. Command and test results include the sandbox backend, granted filesystem roots, network policy, resource limits, command, duration, and denial reason when applicable. Machine-test manifests are also written beside their logs as `*.capabilities.json`.
