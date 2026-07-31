# Agent Farm v0.4.6 for Windows x64

This directory contains the signed Windows preview package for Agent Farm v0.4.6.

## Downloads

- `AgentFarm-Native-0.4.6.0-x64.msix` — signed WinUI 3 desktop application with the frozen Agent
  Farm backend.
- `AgentFarm-dev.cer` — public development certificate required to trust the current preview MSIX.
- `SHA256SUMS.txt` — SHA-256 checksum for package verification.

## Installation

1. Download the MSIX and CER files.
2. Import `AgentFarm-dev.cer` into `Cert:\CurrentUser\TrustedPeople`.
3. Double-click the MSIX and select **Install**.
4. Launch **Agent Farm** from the Start menu.

PowerShell certificate import:

```powershell
Import-Certificate `
  -FilePath .\AgentFarm-dev.cer `
  -CertStoreLocation Cert:\CurrentUser\TrustedPeople
```

## Preview installer notice

The application package is functional and signed, but the one-click installation experience is
still being optimized. The current preview requires manual trust of a development certificate.
Production signing, automatic dependency handling, smaller packages, and update delivery are in
progress.

The private signing key is not included in this repository. Only the public `.cer` certificate is
distributed.
