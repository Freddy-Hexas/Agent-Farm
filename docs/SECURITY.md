# Security model

Agent Farm executes model-generated actions against source repositories. Its controls reduce risk;
they do not make untrusted model output equivalent to trusted human code review.

## Protected assets

- provider API keys and local credentials;
- the Supervisor workspace and Git history;
- files outside the selected repository and allowed paths;
- command execution, network access, and release credentials;
- runtime history, diagnostic bundles, and cost information.

## Boundaries and controls

### Secrets

Provider keys are stored in `.agent-farm/secrets.env` or the process environment. Settings returns
only presence metadata, never the saved secret. Secrets, bearer tokens, and key-like values are
redacted from structured backend/desktop logs and diagnostic bundles. Local config, `.agent-farm/`,
`.env*`, PFX files, build output, and test artifacts are ignored by Git.

### Worker isolation

Each Worker runs in a dedicated Git worktree from an explicit base commit. The no-shell command
runner accepts only supported executable/argument structures, applies a restricted environment,
and gives each command a bounded execution duration. Model inference itself has no client timeout.
In the default `workspace-write` mode, repository code is copied into a Docker container with
`--network=none`, a read-only root filesystem, dropped Linux capabilities, and memory/CPU/PID
limits. A missing Docker daemon or language image is reported as unavailable; there is no silent
host-execution fallback.

Plans declare `allowed_paths` and `forbidden_paths`. The runtime checks those boundaries during tool
use and machine review. Secret files, `.git`, CI workflows, and sensitive configuration are denied
by default. Workers produce candidate patches and evidence; they cannot push, deploy, change
permissions, or write directly into the Supervisor workspace.

### Network and approvals

Network tools are disabled unless the selected configuration enables them. Commands, file writes,
and network operations can create durable approval requests. Decisions are explicit:
`allow_once`, `allow_session`, `deny`, or `cancel`. Approval state survives a client reconnect.

Public research is bounded by tool-call and extraction limits. Agent Farm does not bypass
authentication, paywalls, anti-bot controls, or access restrictions.

### Local runtime

The API binds to loopback. The desktop discovers it through a repository-local descriptor and
validates protocol capabilities and source fingerprint. Requests carry correlation IDs. Jobs and
events are durable, cancellation is scoped, unclean shutdowns are reconciled, and diagnostic export
is sanitized and retention-limited.

### Updates and supply chain

Production MSIX packaging requires a CA-issued certificate whose subject matches the manifest and a
trusted timestamp. Release secrets are scoped to a protected GitHub environment. The in-app updater
accepts only HTTPS GitHub release origins and verifies SHA256 before launching the package.

CI runs Python and NuGet dependency audits, CodeQL for Python and C#, and Gitleaks across repository
history. Dependencies used by the frozen runtime are pinned for reproducible vulnerability scans.

## User responsibilities

- Use narrowly scoped provider keys and rotate any key that may have entered a log or commit.
- Inspect requested approvals, patches, test evidence, and final artifacts before applying changes.
- Use `allowed_paths`, budgets, and network access appropriate to the task.
- Do not run Agent Farm against a repository containing secrets that Workers do not need.
- Require human review for production deployments and for financial, medical, legal, safety, or
  other high-impact decisions.
- Trust only a release whose signer, timestamp, and checksum you verified.

## Reporting a vulnerability

Do not open a public issue containing an exploit, key, private repository content, or diagnostic
bundle. Use GitHub's private security advisory flow for `Freddy-Hexas/Agent-Farm` and include the
affected version, impact, minimal reproduction, and suggested mitigation. Revoke exposed
credentials before sharing any sanitized evidence.
