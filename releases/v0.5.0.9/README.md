# Agent Farm 0.5.0.9 preview package

This directory contains the self-contained Windows x64 package built from the Agent Farm 0.5.0.9 source. It includes the native WinUI 3 desktop client, the .NET and Windows App SDK runtime files, and the frozen Python product backend.

## Highlights

- New Agent Farm application icon rebuilt from the original PowerPoint vector artwork, with dedicated 16-48 px rendering for sharper Windows title-bar and taskbar display.
- A native Settings back button, surface history, and `Alt+Left` navigation.
- Reliable daemon upgrade handoff when a new desktop build replaces an older repository runtime.
- Cleaner handling of expected loopback connection resets.
- Release staging now keeps the packaged backend and WinUI assets synchronized with local Release smoke tests.
- GitHub Actions now use current Node 24-based action versions, install the complete test dependencies, and select only GA .NET SDK builds.

## Files

- `AgentFarm-Native-x64.msix` - self-contained, development-signed preview installer.
- `AgentFarm-dev.cer` - public development certificate for this checked-in preview package.
- `SHA256SUMS.txt` - SHA-256 hashes for the MSIX and AppInstaller metadata.
- `AgentFarm-stable.appinstaller` - Stable-channel update metadata for publication with the matching GitHub Release assets.

## Install the preview

1. Verify `AgentFarm-Native-x64.msix` against `SHA256SUMS.txt`.
2. Inspect `AgentFarm-dev.cer`, then import it into the current user's **Trusted People** store:

```powershell
Import-Certificate `
  -FilePath .\AgentFarm-dev.cer `
  -CertStoreLocation Cert:\CurrentUser\TrustedPeople
```

3. Double-click `AgentFarm-Native-x64.msix` and choose **Install**.
4. Launch **Agent Farm** from the Start menu.

This is a development-signed preview package. The production release workflow requires a protected CA-issued signing certificate.

## Verification performed

- 222 Python tests and 36 subtests passed.
- Native MVVM state tests passed.
- Release WinUI build completed with 0 warnings and 0 errors.
- A real Release launch reached a healthy frozen backend with runtime fingerprint `1.0.0.0`.
- Settings navigation and the native back action passed UI automation.
- Frozen backend health and diagnostic-export smoke tests passed.
- The packaged icon and logo bytes match the authoritative WinUI assets.
- Package version `0.5.0.9`, Authenticode signature, DigiCert timestamp, and SHA-256 checksums were validated.

## SHA-256

`AgentFarm-Native-x64.msix`:

```text
05654e64ff5514003b000e6c2c9d125b7e456045b6b4b5221d5caea7490295d8
```
