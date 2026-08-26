# Agent Farm for Windows

This is the self-contained x64 desktop release. It includes the native WinUI client, Windows App SDK runtime, and frozen Agent Farm backend. Python is not required.

## Install

Double-click `Install-AgentFarm.cmd` and approve the Windows UAC prompt. The installer verifies the MSIX signature, trusts the matching development certificate on this machine, then installs or updates Agent Farm.

If installation fails, the launcher prints the exit code and writes the detailed PowerShell error to `Install-AgentFarm.log` in the same folder.

After installation, open **Agent Farm** from the Windows Start menu.

## Release files

- `AgentFarm-Native-x64.msix`: the application package.
- `AgentFarm-dev.cer`: the public certificate that signs this development release.
- `Install-AgentFarm.cmd`: one-click elevated installer.
- `SHA256SUMS.txt`: SHA-256 checksums for every release asset.

## Notes

This package is development-signed for local preview distribution. A public production release must use a CA-issued code-signing certificate. AppInstaller metadata is generated only by the full release pipeline after the matching assets are published to the configured GitHub Release.
