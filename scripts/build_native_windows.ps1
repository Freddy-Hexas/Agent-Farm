param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [ValidateSet("x64", "x86", "ARM64")]
    [string]$Platform = "x64",
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopProject = Join-Path $projectRoot "AgentFarm.Desktop\AgentFarm.Desktop.csproj"
$workflow = Join-Path $env:USERPROFILE ".codex\plugins\cache\microsoft-winui\winui\0.3.0\skills\winui-dev-workflow\BuildAndRun.ps1"

if ($Configuration -eq "Release" -and -not $SkipBackend) {
    Push-Location $projectRoot
    try {
        python -m PyInstaller --noconfirm --clean "packaging\AgentFarmBackend.spec"
        if ($LASTEXITCODE -ne 0) {
            throw "The Agent Farm backend build failed."
        }
    }
    finally {
        Pop-Location
    }
}
elseif ($Configuration -eq "Debug") {
    Write-Host "Debug build: using the Python source runtime; skipping PyInstaller."
}

if (-not (Test-Path $workflow)) {
    throw "The WinUI build workflow was not found. Install the Microsoft WinUI plugin first."
}

& $workflow `
    -Project $desktopProject `
    -SkipRun `
    -ExtraArgs "/p:Platform=$Platform", "/p:Configuration=$Configuration"

if ($LASTEXITCODE -ne 0) {
    throw "The Agent Farm desktop build failed."
}

$output = Join-Path $projectRoot "AgentFarm.Desktop\bin\$Platform\$Configuration"
Write-Host "Native Agent Farm build completed: $output"
