param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+\.\d+$')]
    [string]$Version,
    [ValidateSet("stable", "preview")]
    [string]$Channel = "stable",
    [ValidateSet("x64")]
    [string]$Platform = "x64",
    [Parameter(Mandatory)]
    [string]$PfxPath,
    [string]$PfxPassword = $env:AGENT_FARM_SIGNING_PASSWORD,
    [string]$Publisher = "CN=Agent Farm",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SkipBuild,
    [switch]$AllowDevelopmentCertificate
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifestPath = Join-Path $projectRoot "AgentFarm.Desktop\Package.appxmanifest"
$channelConfigPath = Join-Path $projectRoot "packaging\release_channels.json"
$outputRoot = Join-Path $projectRoot "dist\release\$Channel\$Version"
$releaseOutput = Join-Path $projectRoot "AgentFarm.Desktop\bin\$Platform\Release\net10.0-windows10.0.26100.0\win-$Platform"

if (-not $PfxPassword) { throw "PfxPassword or AGENT_FARM_SIGNING_PASSWORD is required." }
$resolvedPfx = (Resolve-Path -LiteralPath $PfxPath).Path
$certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
    $resolvedPfx,
    $PfxPassword,
    [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
)
if (-not $certificate.HasPrivateKey) { throw "The signing certificate does not contain a private key." }
if ($certificate.Subject -ne $Publisher) {
    throw "Certificate subject '$($certificate.Subject)' does not match manifest publisher '$Publisher'."
}
if ($certificate.NotAfter.ToUniversalTime() -lt [DateTime]::UtcNow.AddDays(30)) {
    throw "The signing certificate expires in fewer than 30 days."
}
if (-not $AllowDevelopmentCertificate -and $certificate.Subject -eq $certificate.Issuer) {
    throw "Production releases reject self-signed certificates. Use a CA-issued code-signing certificate."
}

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot "build_native_windows.ps1") -Configuration Release -Platform $Platform
    if ($LASTEXITCODE -ne 0) { throw "The native Release build failed." }
}
if (-not (Test-Path -LiteralPath (Join-Path $releaseOutput "AgentFarm.Desktop.exe"))) {
    throw "Release output is missing: $releaseOutput"
}
& (Join-Path $PSScriptRoot "test_frozen_backend.ps1") -ReleaseOutput $releaseOutput
if ($LASTEXITCODE -ne 0) { throw "The frozen backend smoke test failed." }

$channels = Get-Content -LiteralPath $channelConfigPath -Raw | ConvertFrom-Json
$policy = $channels.channels.$Channel
if ($null -eq $policy) { throw "Unknown release channel: $Channel" }
$repository = [string]$channels.repository
$releaseBase = "https://github.com/$repository/releases/$($policy.release_path)"
$appInstallerUri = "$releaseBase/$($policy.appinstaller_asset)"
$packageUri = "$releaseBase/$($policy.package_asset)"
$versionParts = @($Version.Split('.') | ForEach-Object { [int]$_ })
# The 2018 AppInstaller schema requires its own version's major component to
# be non-zero. Keep it monotonic and independent from the MSIX identity by
# offsetting the package major; MainPackage still uses the exact MSIX version.
$appInstallerVersion = "{0}.{1}.{2}.{3}" -f (
    $versionParts[0] + 1), $versionParts[1], $versionParts[2], $versionParts[3]

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$packagePath = Join-Path $outputRoot ([string]$policy.package_asset)
$appInstallerPath = Join-Path $outputRoot ([string]$policy.appinstaller_asset)
$checksumsPath = Join-Path $outputRoot "SHA256SUMS.txt"
foreach ($path in @($packagePath, $appInstallerPath, $checksumsPath)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}

$temporaryManifest = Join-Path ([IO.Path]::GetTempPath()) "AgentFarm-$Version-$([Guid]::NewGuid().ToString('N')).appxmanifest"
try {
    [xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw
    $identity = $manifest.SelectSingleNode("/*[local-name()='Package']/*[local-name()='Identity']")
    if ($null -eq $identity) { throw "Package.appxmanifest has no Identity element." }
    $identity.SetAttribute("Version", $Version)
    $identity.SetAttribute("Publisher", $Publisher)
    $manifest.Save($temporaryManifest)

    winapp package $releaseOutput `
        --manifest $temporaryManifest `
        --output $packagePath `
        --executable AgentFarm.Desktop.exe `
        --quiet
    if ($LASTEXITCODE -ne 0) { throw "winapp package failed." }

    winapp sign $packagePath $resolvedPfx `
        --password $PfxPassword `
        --timestamp $TimestampUrl `
        --quiet
    if ($LASTEXITCODE -ne 0) { throw "winapp sign failed." }
}
finally {
    Remove-Item -LiteralPath $temporaryManifest -Force -ErrorAction SilentlyContinue
    $certificate.Dispose()
}

$signature = Get-AuthenticodeSignature -LiteralPath $packagePath
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "MSIX signature validation failed: $($signature.StatusMessage)"
}
if ($null -eq $signature.TimeStamperCertificate) {
    throw "The MSIX is signed but does not contain a trusted timestamp."
}

$hours = [int]$policy.hours_between_update_checks
$xmlPublisher = [Security.SecurityElement]::Escape($Publisher)
$xmlAppInstallerUri = [Security.SecurityElement]::Escape($appInstallerUri)
$xmlPackageUri = [Security.SecurityElement]::Escape($packageUri)
$appInstaller = @"
<?xml version="1.0" encoding="utf-8"?>
<AppInstaller Uri="$xmlAppInstallerUri"
              Version="$appInstallerVersion"
              xmlns="http://schemas.microsoft.com/appx/appinstaller/2018">
  <MainPackage Name="AgentFarm.Desktop"
               Publisher="$xmlPublisher"
               Version="$Version"
               ProcessorArchitecture="$Platform"
               Uri="$xmlPackageUri" />
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="$hours"
              ShowPrompt="true"
              UpdateBlocksActivation="false" />
    <AutomaticBackgroundTask />
    <ForceUpdateFromAnyVersion>false</ForceUpdateFromAnyVersion>
  </UpdateSettings>
</AppInstaller>
"@
[IO.File]::WriteAllText($appInstallerPath, $appInstaller, [Text.UTF8Encoding]::new($false))

$hashLines = foreach ($path in @($packagePath, $appInstallerPath)) {
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($path))"
}
[IO.File]::WriteAllLines($checksumsPath, $hashLines, [Text.UTF8Encoding]::new($false))

[pscustomobject]@{
    version = $Version
    appinstaller_version = $appInstallerVersion
    channel = $Channel
    package = $packagePath
    appinstaller = $appInstallerPath
    checksums = $checksumsPath
    signer = $signature.SignerCertificate.Subject
    timestamped = $null -ne $signature.TimeStamperCertificate
} | ConvertTo-Json
