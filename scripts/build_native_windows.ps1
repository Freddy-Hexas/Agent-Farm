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
$runtimeOutput = Join-Path $projectRoot "AgentFarm.Desktop\bin\$Platform\$Configuration\net8.0-windows10.0.26100.0\win-$($Platform.ToLowerInvariant())"
$backendTarget = Join-Path $runtimeOutput "Backend"
$appxBackendTarget = Join-Path $runtimeOutput "AppX\Backend"

$pythonCandidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot "venv\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
    (Join-Path $env:USERPROFILE "anaconda3\python.exe")
)
$pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pathPython -and $pathPython.Source -notmatch "WindowsApps") {
    $pythonCandidates += $pathPython.Source
}
$python = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $python) {
    throw "A usable Python interpreter was not found. Install Python 3.11 or newer or activate the project environment."
}

if ($Configuration -eq "Release" -and (Test-Path -LiteralPath $backendTarget)) {
    $resolvedOutput = [IO.Path]::GetFullPath($runtimeOutput).TrimEnd('\') + '\'
    $resolvedTarget = [IO.Path]::GetFullPath($backendTarget)
    if (-not $resolvedTarget.StartsWith($resolvedOutput, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a backend target outside the Release output: $resolvedTarget"
    }
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}

if ($Configuration -eq "Release" -and -not $SkipBackend) {
    Push-Location $projectRoot
    try {
        & $python -m PyInstaller --noconfirm --clean "packaging\AgentFarmBackend.spec"
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

if ($Configuration -eq "Release") {
    $backendSource = Join-Path $projectRoot "dist\AgentFarmBackend"
    if (-not (Test-Path -LiteralPath (Join-Path $backendSource "AgentFarmBackend.exe"))) {
        throw "The frozen backend is missing: $backendSource"
    }
    Copy-Item -LiteralPath $backendSource -Destination $backendTarget -Recurse -Force

    # `winapp run` launches the AppX staging tree when it is present. Keep its
    # backend in lockstep with the runtime-output copy so local Release smoke
    # tests exercise the exact backend that will be packaged.
    if (Test-Path -LiteralPath (Join-Path $runtimeOutput "AppX")) {
        if (Test-Path -LiteralPath $appxBackendTarget) {
            Remove-Item -LiteralPath $appxBackendTarget -Recurse -Force
        }
        Copy-Item -LiteralPath $backendSource -Destination $appxBackendTarget -Recurse -Force
    }

    # The custom release packager consumes the runtime output root. Copy the
    # authoritative project assets directly instead of relying on AppX\Assets,
    # which can retain stale incremental-build files outside MSBuild's clean
    # manifest.
    $appAssetsSource = Join-Path $projectRoot "AgentFarm.Desktop\Assets"
    $packageAssetsTarget = Join-Path $runtimeOutput "Assets"
    if (-not (Test-Path -LiteralPath (Join-Path $appAssetsSource "AppIcon.ico"))) {
        throw "The WinUI project assets are missing: $appAssetsSource"
    }
    New-Item -ItemType Directory -Force -Path $packageAssetsTarget | Out-Null
    Get-ChildItem -LiteralPath $appAssetsSource -File | Copy-Item -Destination $packageAssetsTarget -Force

    $appxAssetsTarget = Join-Path $runtimeOutput "AppX\Assets"
    if (Test-Path -LiteralPath (Join-Path $runtimeOutput "AppX")) {
        New-Item -ItemType Directory -Force -Path $appxAssetsTarget | Out-Null
        Get-ChildItem -LiteralPath $appAssetsSource -File | Copy-Item -Destination $appxAssetsTarget -Force
    }
}

Write-Host "Native Agent Farm build completed: $runtimeOutput"
