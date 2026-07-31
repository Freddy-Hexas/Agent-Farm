param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$iconPath = Join-Path $repoRoot "build\packaging\agent-farm.ico"
$specPath = Join-Path $repoRoot "packaging\AgentFarm.spec"

Push-Location $repoRoot
try {
    if (-not $SkipTests) {
        python -m unittest discover -s tests
        if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
    }

    python packaging\make_icon.py $iconPath
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }

    python -m PyInstaller --noconfirm --clean $specPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $exePath = Join-Path $repoRoot "dist\AgentFarm.exe"
    if (-not (Test-Path $exePath)) { throw "AgentFarm.exe was not produced." }
    Write-Host "Built $exePath"
}
finally {
    Pop-Location
}
