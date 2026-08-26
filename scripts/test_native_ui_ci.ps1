param(
    [switch]$ReadyOnly
)

$ErrorActionPreference = "Stop"
$artifactRoot = Join-Path $PSScriptRoot "..\test-artifacts\native-ui-tests"
New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
$appPid = 0
$launchOutput = @()
$readyOutput = @()

function ConvertTo-GitHubCommandValue {
    param([string]$Value)
    return $Value.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
}

function Add-DiagnosticLog {
    param(
        [Collections.Generic.List[string]]$Diagnostics,
        [string]$Path
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    Copy-Item -LiteralPath $Path -Destination $artifactRoot -Force
    $Diagnostics.Add("--- $Path ---")
    Get-Content -LiteralPath $Path -Tail 80 | ForEach-Object { $Diagnostics.Add("$_") }
}

try {
    $workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $python = (Get-Command python.exe -ErrorAction Stop).Path
    if (-not $python) {
        throw "The setup-python interpreter could not be resolved."
    }
    $env:AGENT_FARM_REPO = $workspace
    $env:AGENT_FARM_SOURCE_ROOT = $workspace
    $env:AGENT_FARM_PYTHON = $python

    $output = Resolve-Path (Join-Path $workspace "AgentFarm.Desktop\bin\x64\Debug\net8.0-windows10.0.26100.0\win-x64")
    $launchOutput = @(& winapp run $output --detach --json 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "winapp run failed with exit code $LASTEXITCODE.`n$($launchOutput -join "`n")"
    }
    $launchText = $launchOutput -join "`n"
    $jsonStart = $launchText.IndexOf('{')
    if ($jsonStart -lt 0) {
        throw "winapp run did not return a JSON launch result.`n$launchText"
    }
    $launch = $launchText.Substring($jsonStart) | ConvertFrom-Json
    $appPid = [int]$launch.ProcessId
    if ($appPid -le 0) {
        throw "winapp run returned an invalid process id."
    }

    $readyOutput = @(& winapp ui wait-for RepositoryButton -a $appPid -t 60000 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "The Agent Farm workspace did not become ready.`n$($readyOutput -join "`n")"
    }
    $readyOutput | Write-Host

    if (-not $ReadyOnly) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test_native_ui.ps1") -AppPid $appPid
        if ($LASTEXITCODE -ne 0) {
            throw "The native UI assertion batch failed with exit code $LASTEXITCODE."
        }
    }
}
catch {
    $diagnostics = [Collections.Generic.List[string]]::new()
    $diagnostics.Add("$($_.Exception)")
    $launchOutput | ForEach-Object { $diagnostics.Add("$_") }
    $readyOutput | ForEach-Object { $diagnostics.Add("$_") }
    if ($appPid -gt 0) {
        @(& winapp ui list-windows -a $appPid 2>&1) | ForEach-Object { $diagnostics.Add("$_") }
        $process = Get-Process -Id $appPid -ErrorAction SilentlyContinue
        if ($process) {
            $diagnostics.Add(($process | Format-List Id,Name,HasExited,Path | Out-String).Trim())
        }
    }
    Add-DiagnosticLog $diagnostics (Join-Path $workspace ".agent-farm\logs\runtime.log")
    Add-DiagnosticLog $diagnostics (Join-Path $env:LOCALAPPDATA "Agent Farm\Logs\desktop-events.jsonl")
    $detail = ConvertTo-GitHubCommandValue (($diagnostics | Select-Object -Last 160) -join "`n")
    Write-Output "::error title=Native UI startup::$detail"
    exit 1
}
finally {
    if ($appPid -gt 0) {
        try {
            (Get-Process -Id $appPid -ErrorAction SilentlyContinue).CloseMainWindow() | Out-Null
        }
        catch {
            # The process may already have exited after a startup failure.
        }
    }
}
