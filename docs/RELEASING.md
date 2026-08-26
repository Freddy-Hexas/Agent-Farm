# Releasing Agent Farm for Windows

This procedure applies to Agent Farm 0.5.0.13 and later. Release builds are x64, self-contained WinUI
3 applications with a frozen Python backend.

## Release channels

`packaging/release_channels.json` is authoritative:

| Channel | GitHub release | AppInstaller cadence | In-app cadence |
| --- | --- | --- | --- |
| Stable | normal versioned release, resolved through `latest` | 4 hours | 24 hours |
| Preview | floating `preview` prerelease | 1 hour | 4 hours |

AppInstaller provides Windows-managed upgrades when the host serves the correct MIME type. The
native updater is a fallback: it queries GitHub Releases, accepts only approved HTTPS origins,
downloads `SHA256SUMS.txt`, verifies the MSIX hash, and then opens Windows App Installer.

## Production signing prerequisites

Configure the protected GitHub `production-signing` environment and these secrets:

- `AGENT_FARM_SIGNING_PFX_BASE64`: base64-encoded CA-issued code-signing PFX;
- `AGENT_FARM_SIGNING_PASSWORD`: PFX password;
- `AGENT_FARM_PUBLISHER`: exact certificate subject used in the MSIX manifest.

Require reviewer approval for that environment. Never store the PFX or password in Git, artifacts,
logs, repository variables, or a developer certificate directory. The packaging script rejects a
self-signed certificate unless the explicit development-only switch is used.

## Pre-release verification

1. Synchronize version values in `pyproject.toml`, `agent_farm/__init__.py`,
   `Package.appxmanifest`, and `packaging/version_info.txt`.
2. Update README, architecture, protocol, security, and limitations documentation.
3. Run:

```powershell
python -m pytest -q
dotnet run --project .\AgentFarm.Desktop.StateTests\AgentFarm.Desktop.StateTests.csproj
.\scripts\build_native_windows.ps1 -Configuration Debug -Platform x64
.\scripts\build_native_windows.ps1 -Configuration Release -Platform x64
pip-audit --requirement .\packaging\runtime-requirements.txt --strict --vulnerability-service osv
dotnet list .\AgentFarm.Desktop\AgentFarm.Desktop.csproj package --vulnerable --include-transitive
```

Run `scripts/test_native_ui.ps1` against a `winapp run` Debug launch. Run a real Supervisor + Worker
workflow with the intended provider routes and inspect its stream, patch, tests, and diagnostic
bundle.

## Local packaging rehearsal

Development certificates are only for validating the mechanics:

```powershell
.\scripts\package_native_release.ps1 `
  -Version 0.5.0.13 `
  -Channel stable `
  -PfxPath .\dist\signing\AgentFarm-dev.pfx `
  -PfxPassword password `
  -AllowDevelopmentCertificate
```

The script builds Release, tests the frozen backend, packages the self-contained output, signs with
a trusted timestamp, verifies Authenticode, and writes the AppInstaller and checksums below
`dist/release/<channel>/<version>/`.

A development-signed MSIX may fail `Add-AppxPackage` until its certificate is trusted at the scope
required by Windows. That does not substitute for production install validation.

## Production workflow

Run **Signed Windows release** from GitHub Actions with a four-part MSIX version, channel, and
`publish=true`. The protected workflow:

1. runs the full test suite;
2. decodes the protected certificate only on the ephemeral runner;
3. builds, signs, and timestamps the package;
4. uploads the MSIX, AppInstaller, and checksums as evidence;
5. installs the signed MSIX and verifies the registered version;
6. publishes exact Stable assets or replaces the floating Preview assets.

The fixed asset names `AgentFarm-Native-x64.msix`, `SHA256SUMS.txt`, and the channel AppInstaller are
part of the update contract. Do not rename them without updating both channel policy and the native
updater.

## Post-release checks and rollback

- Verify Authenticode status, signer subject, and timestamp certificate.
- Verify the MSIX contains `AgentFarm.Desktop.exe`, `Microsoft.WindowsAppRuntime.dll`, and
  `Backend/AgentFarmBackend.exe`.
- Install on a clean x64 Windows account, create a repository, configure a provider, and run a farm.
- Check Stable/Preview discovery and SHA verification from Settings.
- Export and inspect a sanitized diagnostic bundle.

If a release is unsafe, remove its GitHub Release assets, publish a fixed higher four-part version,
and document the incident. Normal Windows package identity prevents silent downgrade. Emergency
rollback requires an explicitly signed higher version containing the reverted code.
