# Current limitations

This list is synchronized with Agent Farm 0.5.0.9.

- The checked-in preview MSIX is development-signed and requires manual certificate trust. The
  production workflow is implemented, but the project owner must supply a CA-issued certificate and
  publish the public Stable/Preview channels.
- Release packages currently target Windows x64 only.
- Automatic updates require the fixed MSIX and checksum assets to be published on GitHub Releases.
- Agent Farm requires Git and a valid repository because Worker isolation is worktree-based.
- Secure execution of repository code in the default `workspace-write` mode requires Docker Desktop
  and the corresponding language image to be available locally. Image acquisition depends on the
  user's registry and network configuration.
- Provider model catalogs, reasoning controls, token usage, and error semantics vary. A vendor that
  implements only part of an OpenAI-compatible API may need a custom route or adapter.
- Pricing estimates depend on the maintained pricing catalog and provider-reported token usage; a
  provider invoice remains authoritative.
- Public web extraction cannot bypass logins, paywalls, anti-bot pages, robots restrictions, or
  inaccessible content. Research sources still require human judgment.
- Very large repositories, diffs, binary artifacts, or many simultaneous Workers can consume
  substantial disk space. Retention cleans runtime backups and diagnostics, but deliberate worktree
  cleanup may still be necessary.
- Reconnect and crash recovery preserve durable state, but an external provider request interrupted
  at the network/process boundary may need an explicit retry.
- Worker isolation, approvals, and deterministic review reduce risk; they do not prove semantic
  correctness or eliminate the need for human review in high-impact work.

Planned work is tracked in [ROADMAP.md](ROADMAP.md). Operational controls are documented in
[SECURITY.md](SECURITY.md) and [RELEASING.md](RELEASING.md).
