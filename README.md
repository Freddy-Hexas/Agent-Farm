# Agent Farm

**One expensive brain. Many economical hands.**

Agent Farm is a Windows desktop application for autonomous multi-agent work. A high-capability
**Supervisor** understands the request, creates a bounded plan, routes each task to an independent
**Worker**, reviews the evidence, and produces the final result. Workers can use substantially less
expensive models than the Supervisor.

[![Version](https://img.shields.io/badge/version-0.4.6-blue)](releases/v0.4.6)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-0078D4)](releases/v0.4.6)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> [!IMPORTANT]
> Agent Farm is an active preview. The signed MSIX is usable today, but the one-click installation
> experience is still being optimized. The current preview uses a development signing certificate,
> so first-time installation requires importing the included `.cer` certificate before opening the
> MSIX. Production signing, smoother prerequisite handling, and a true one-click installer are on
> the roadmap.

## Why Agent Farm exists

Most agent products use one model for every step. That is convenient, but expensive: repository
exploration, web research, mechanical edits, test execution, and final judgment do not all require
the same model capability.

Agent Farm makes the cost boundary explicit:

```text
User request
    |
    v
High-capability Supervisor
    |  understand, decompose, route, review, synthesize
    |
    +----> Economical Worker A ----> isolated worktree ----> artifact + evidence
    +----> Economical Worker B ----> isolated worktree ----> artifact + evidence
    +----> Local Worker C ---------> isolated worktree ----> artifact + evidence
    |
    v
Machine review gates
    |
    v
Supervisor decision or final synthesis
```

The Supervisor may use a premium model, while Workers independently use DeepSeek, Qwen, Kimi,
OpenRouter models, local Ollama models, or any OpenAI-compatible endpoint. Model routes are visible
and editable; Agent Farm never silently replaces the selected Worker provider with the Supervisor
provider.

## What is implemented

### Native Windows desktop application

- Native WinUI 3 shell with Windows App SDK.
- Taskbar-safe startup maximization and native window controls.
- Persistent project threads and task history.
- Settings, Agents, Providers, Runs, and evidence views.
- Local loopback backend owned by the desktop process.
- Release builds include a frozen Python backend and do not require a system Python installation.
- Debug builds start the Python backend directly from source for fast iteration.

### Real multi-agent orchestration

- A high-capability Supervisor creates validated Worker Plans.
- Every Worker selects an explicit named model profile.
- Workers run concurrently up to a configured limit.
- Every Worker receives an independent Git worktree.
- Workers can inspect files, search code, edit bounded paths, and run verification commands.
- Research Workers can use opt-in public web search and bounded page/PDF extraction.
- Collaborative tasks can combine every passing Worker artifact into one final deliverable.
- Typed JSONL events preserve messages, tool calls, results, usage, and failures.

### Review and safety boundaries

- `allowed_paths` restrict where a Worker may write.
- `forbidden_paths` protect secrets, Git metadata, CI workflows, and other sensitive paths.
- Commands are executed through a bounded no-shell runner.
- Worker changes are collected as patches before any Supervisor decision.
- Machine review checks path scope, changed-file count, diff size, lockfiles, test deletion, and
  configured verification commands.
- Provider credentials are stored locally and never returned by the Settings API.
- Network tools are disabled unless network access is explicitly enabled.
- Workers cannot push, deploy, change permissions, or silently modify the Supervisor workspace.

### Provider-aware model routing

Agent Farm includes templates for the following providers and runtimes:

| Category | Providers |
| --- | --- |
| Direct providers | OpenAI, Anthropic Claude, Google Gemini, xAI, Mistral AI |
| China-region providers | DeepSeek, Kimi, Alibaba Cloud Qwen, Zhipu GLM, Volcengine Ark / Doubao |
| Model gateways | SiliconFlow, OpenRouter, GroqCloud, Together AI, Fireworks AI |
| Local runtimes | Ollama, LM Studio |
| Custom | Any OpenAI-compatible Chat Completions or Responses endpoint |

Official templates load the compatible model catalog available to the configured API key and show
the models as a dropdown. Custom OpenAI-compatible routes retain a manual Model ID field.

Reasoning controls are provider-aware. Agent Farm does not show OpenAI-only effort values for a
provider that does not support them. For example, the DeepSeek route uses its supported thinking
control and `high` / `max` effort values rather than OpenAI's `xhigh` UI.

## Architecture

```text
AgentFarm.Desktop (WinUI 3)
    |
    +-- WebView2 workspace UI
    +-- native title bar and window lifecycle
    +-- starts/stops the local backend
    |
    v
Loopback product API
    |
    +-- thread store
    +-- settings and secret store
    +-- provider catalog discovery
    +-- Supervisor planning and review
    +-- farm scheduler
    |
    v
Native agent runtime
    |
    +-- Responses API client
    +-- Chat Completions client
    +-- repository and web tools
    +-- per-Worker Git worktrees
    +-- event logs, patches, tests, and machine review
```

The frontend does not contain a mock agent. Buttons in the desktop workspace call the same backend
used by the CLI, and Settings updates are applied to subsequent runs.

## Installation on Windows

### Requirements

- Windows 10 version 1809 or newer; Windows 11 is recommended.
- x64 processor for the current release package.
- Git installed and available on `PATH`.
- At least one model provider API key, unless all selected routes use local runtimes.
- Microsoft Windows App Runtime 2.3. Windows may install the dependency automatically; dependency
  handling will be improved as part of the one-click installer work.

### Install the current preview

1. Open the [v0.4.6 download directory](releases/v0.4.6).
2. Download:
   - [`AgentFarm-Native-0.4.6.0-x64.msix`](https://github.com/Freddy-Hexas/Agent-Farm/raw/main/releases/v0.4.6/AgentFarm-Native-0.4.6.0-x64.msix)
   - [`AgentFarm-dev.cer`](https://github.com/Freddy-Hexas/Agent-Farm/raw/main/releases/v0.4.6/AgentFarm-dev.cer)
3. Import `AgentFarm-dev.cer` into the current user's **Trusted People** certificate store.
4. Double-click `AgentFarm-Native-0.4.6.0-x64.msix` and select **Install**.
5. Launch **Agent Farm** from the Start menu.

You can import the certificate with PowerShell instead of the certificate wizard:

```powershell
Import-Certificate `
  -FilePath .\AgentFarm-dev.cer `
  -CertStoreLocation Cert:\CurrentUser\TrustedPeople
```

The certificate is a development certificate for preview distribution. Inspect the certificate and
package before trusting them. The MSIX is signed with publisher `CN=Agent Farm`.

> [!NOTE]
> The current package is an upgradeable MSIX, but installation is not yet the final one-click
> experience. We are actively optimizing certificate trust, runtime dependency installation,
> package size, update delivery, and production code signing.

## First run

1. Open Agent Farm and select a Git repository when prompted.
2. Open **Settings → Providers**.
3. Add a provider template and enter its API key. API keys are write-only in the UI.
4. Open **Settings → Agents**.
5. Select the premium Supervisor provider and model.
6. Add or edit one or more Worker routes, then select their provider and model from the catalog.
7. Return to the workspace, describe the desired outcome, and start the task.
8. Follow the plan, Worker activity, evidence, and final deliverable from the same thread.

A typical cost-conscious configuration is:

```text
Supervisor: premium reasoning model
Worker A:   DeepSeek V4 Flash
Worker B:   another economical hosted model
Worker C:   optional local Ollama or LM Studio model
```

## How a task runs

1. **Plan** — the Supervisor inspects the request and creates a structured Worker Plan.
2. **Validate** — Agent Farm rejects duplicate Worker IDs, unknown profiles, unsafe paths, or an
   invalid deliverable location.
3. **Isolate** — one Git worktree is created for each Worker from the same base commit.
4. **Execute** — Workers use their assigned providers and models to perform bounded work.
5. **Collect** — Agent Farm records the patch, changed files, tests, final report, and typed events.
6. **Review** — deterministic machine rules reject unsafe or out-of-scope changes.
7. **Decide or synthesize** — the Supervisor reviews passing evidence and either selects a result or
   combines all passing Worker artifacts into a final deliverable.

Web research is deliberately finite. Research Workers receive a hard tool-call budget and a turn
deadline, after which web tools are removed and the Worker is instructed to write and verify the
requested artifact. This prevents inexpensive models from browsing indefinitely without producing
a result.

## Local configuration

The desktop Settings UI is recommended, but the same routes can be configured in
`agent-farm.local.json`. This file is intentionally ignored by Git.

```json
{
  "agent_backend": "native",
  "supervisor_provider": "openai-main",
  "supervisor_model": "your-premium-model",
  "default_worker_profile": "cheap",
  "max_parallel_workers": 2,
  "model_providers": {
    "openai-main": {
      "template_id": "openai",
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "env_key": "OPENAI_API_KEY",
      "wire_api": "responses"
    },
    "deepseek": {
      "template_id": "deepseek",
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com",
      "env_key": "DEEPSEEK_API_KEY",
      "wire_api": "chat"
    }
  },
  "worker_profiles": {
    "cheap": {
      "display_name": "DeepSeek Worker",
      "provider": "deepseek",
      "model": "deepseek-v4-flash",
      "reasoning_mode": "enabled",
      "timeout_seconds": 1200
    }
  },
  "codex_config_overrides": {
    "sandbox_workspace_write.network_access": true
  }
}
```

Store real credentials only in `.agent-farm/secrets.env` or the process environment:

```dotenv
OPENAI_API_KEY=replace-with-your-key
DEEPSEEK_API_KEY=replace-with-your-key
```

Never commit `agent-farm.local.json`, `.agent-farm/secrets.env`, `.env`, or real API keys.

## Command-line interface

The desktop app is the primary interface, but the CLI remains useful for automation and debugging.

```powershell
# Initialize public and local configuration templates
python -m agent_farm init
python -m agent_farm init-local

# Start the browser-based local console
python -m agent_farm ui --repo .

# Start the compatibility desktop launcher
python -m agent_farm desktop --repo .

# Run a validated multi-Worker plan
python -m agent_farm farm-run `
  --repo . `
  --plan .\worker-plan.json `
  --config .\agent-farm.local.json

# Inspect the aggregate review package
python -m agent_farm farm-review --farm .\.agent-farm\farms\<farm-id>
```

Run `python -m agent_farm --help` to see the single-Worker review, merge, cleanup, and farm decision
commands.

## Development

### Prerequisites

- Python 3.11 or newer.
- .NET 10 SDK.
- Windows App SDK / WinUI 3 build dependencies.
- Microsoft WinApp CLI.
- Git.

Install the Python project with desktop and build dependencies:

```powershell
python -m pip install -e ".[desktop,build]"
```

### Run the Python tests

```powershell
python -m unittest discover -s tests -v
```

The `0.4.6` codebase passes 92 automated tests covering configuration, secrets, provider catalogs,
model protocol translation, the native tool loop, worktree isolation, machine review, Supervisor
planning, collaborative synthesis, desktop runtime behavior, HTTP endpoints, and UI contracts.

### Debug build

Debug launches the Python backend from source, so backend and web UI changes do not require a new
PyInstaller bundle:

```powershell
.\scripts\build_native_windows.ps1 -Configuration Debug -Platform x64
```

Use the WinUI `BuildAndRun.ps1` workflow or `winapp run` to launch a packaged WinUI debug build. Do
not run the generated WinUI executable directly because it requires package identity.

### Release build

Release freezes the backend with PyInstaller and copies it into the WinUI output:

```powershell
.\scripts\build_native_windows.ps1 -Configuration Release -Platform x64
```

The release output is written below:

```text
AgentFarm.Desktop\bin\x64\Release\net10.0-windows10.0.26100.0\win-x64
```

### Create a signed MSIX

```powershell
winapp cert generate `
  --manifest .\AgentFarm.Desktop\Package.appxmanifest `
  --output .\dist\signing\AgentFarm-dev.pfx `
  --export-cer `
  --if-exists overwrite

winapp package `
  .\AgentFarm.Desktop\bin\x64\Release\net10.0-windows10.0.26100.0\win-x64 `
  --cert .\dist\signing\AgentFarm-dev.pfx `
  --output .\dist\AgentFarm-Native-0.4.6.0-x64.msix
```

Do not commit the PFX file. Production distribution should use a protected production certificate
and timestamped signatures.

## Repository layout

```text
AgentFarm.Desktop/       Native WinUI 3 desktop shell
agent_farm/              Python product backend and orchestration runtime
agent_farm/web/          Desktop workspace web assets
docs/                    Product, architecture, and roadmap documents
examples/                Safe configuration and plan examples
packaging/               PyInstaller entry points and build specifications
scripts/                 Windows build scripts
tests/                   Automated unit, integration, and UI-contract tests
```

Important backend modules:

| Module | Responsibility |
| --- | --- |
| `agent_farm/model_client.py` | Responses and Chat Completions protocol clients |
| `agent_farm/native_agent.py` | Multi-turn tool loop and bounded tool runtime |
| `agent_farm/supervisor.py` | Planning, review, and collaborative synthesis |
| `agent_farm/farm.py` | Parallel Worker orchestration and farm state |
| `agent_farm/orchestrator.py` | Per-Worker lifecycle and worktree execution |
| `agent_farm/review.py` | Deterministic machine review gates |
| `agent_farm/provider_templates.py` | Built-in provider connection templates |
| `agent_farm/provider_catalog.py` | Live provider model discovery and metadata |
| `agent_farm/web_server.py` | Loopback desktop API |
| `agent_farm/threads.py` | Persistent threads, turns, and event storage |

## Current limitations

- The Windows installer still requires manual trust of a development certificate.
- The current release is x64-only.
- Production code signing and automatic update delivery are not yet implemented.
- Windows App Runtime dependency handling is not yet fully self-contained.
- Provider compatibility can vary when a vendor exposes only a partial OpenAI-compatible API.
- Web extraction cannot bypass authentication, paywalls, anti-bot pages, or inaccessible sources.
- Agent Farm enforces engineering boundaries, but model output still requires appropriate human
  judgment for high-risk code, financial, medical, legal, or production decisions.

## Roadmap

- Production-signed one-click Windows installer.
- Smaller and self-contained release packages.
- Automatic update checks and in-app upgrades.
- Better per-task token, latency, and cost accounting.
- More deterministic retry and recovery controls.
- Richer diff, artifact, and evidence review in the desktop UI.
- Reusable task templates and scheduled farms.
- Additional provider-native protocol adapters where compatibility layers are incomplete.

See [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/PRODUCT_KERNEL_PLAN.md](docs/PRODUCT_KERNEL_PLAN.md)
for the longer-term product direction.

## Contributing

Issues and focused pull requests are welcome. Before submitting a change:

1. Keep provider credentials and local configuration out of the repository.
2. Add or update tests for behavior changes.
3. Run the full Python test suite.
4. For desktop changes, build the WinUI project for x64 Debug and Release as appropriate.
5. Document new configuration fields, provider behavior, and user-visible limitations.

## License

Agent Farm is released under the [MIT License](LICENSE).

## Star History

<p align="center">
  <a href="https://github.com/Freddy-Hexas/Agent-Farm/stargazers">
    <img src="docs/star-history.svg" alt="Agent Farm GitHub star history" width="960">
  </a>
</p>

<p align="center">
  <sub>Updated daily from aggregate GitHub star counts. No Stargazer identities are collected.</sub>
</p>
