#Requires -RunAsAdministrator
[CmdletBinding()]
param()

$releaseRoot = Split-Path -Parent $PSCommandPath
$logPath = Join-Path $releaseRoot "Install-AgentFarm.log"

try {
    $ErrorActionPreference = "Stop"
$certificatePath = Join-Path $releaseRoot "AgentFarm-dev.cer"
$packagePath = Join-Path $releaseRoot "AgentFarm-Native-x64.msix"

foreach ($path in @($certificatePath, $packagePath)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Release asset is missing: $path"
    }
}

$certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($certificatePath)
foreach ($storePath in @("Cert:\LocalMachine\Root", "Cert:\LocalMachine\TrustedPeople")) {
    $trusted = Get-ChildItem $storePath |
        Where-Object { $_.Thumbprint -eq $certificate.Thumbprint } |
        Select-Object -First 1
    if (-not $trusted) {
        Import-Certificate -FilePath $certificatePath -CertStoreLocation $storePath | Out-Null
    }
}

# A self-signed development package reports UnknownError until its root has
# been trusted. Validate the package after importing the bundled certificate.
$signature = Get-AuthenticodeSignature -LiteralPath $packagePath
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "The MSIX signature is not valid: $($signature.StatusMessage)"
}
if ($signature.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
    throw "The bundled certificate does not match the MSIX signing certificate."
}

Add-AppxPackage -Path $packagePath -ForceUpdateFromAnyVersion -ErrorAction Stop
$installed = Get-AppxPackage -Name "AgentFarm.Desktop" -ErrorAction Stop
Write-Host "Agent Farm $($installed.Version) installed successfully."
    Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue
}
catch {
    $details = ($_ | Out-String).Trim()
    try {
        Set-Content -LiteralPath $logPath -Value $details -Encoding UTF8
    }
    catch { }
    Write-Error $details
    exit 1
}
