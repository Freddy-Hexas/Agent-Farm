param(
    [Parameter(Mandatory)]
    [string]$AppOutput,
    [Parameter(Mandatory)]
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$descriptorPath = Join-Path $RepoRoot ".agent-farm\runtime.json"
$desktopPids = [System.Collections.Generic.List[int]]::new()
$ownedDaemonPids = [System.Collections.Generic.HashSet[int]]::new()

function Get-HealthyDescriptor {
    if (-not (Test-Path -LiteralPath $descriptorPath)) {
        return $null
    }
    try {
        $descriptor = Get-Content -LiteralPath $descriptorPath -Raw | ConvertFrom-Json
        $authority = ([Uri]$descriptor.url).GetLeftPart([UriPartial]::Authority)
        $health = Invoke-RestMethod -Uri "$authority/api/health" -TimeoutSec 2
        if ($health.status -ne "ok" -or $health.protocol_version -ne 1) {
            return $null
        }
        return $descriptor
    }
    catch {
        return $null
    }
}

function Wait-HealthyDescriptor {
    param(
        [int]$TimeoutSeconds = 20,
        [int]$ExceptPid = 0
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $descriptor = Get-HealthyDescriptor
        if ($null -ne $descriptor -and ($ExceptPid -eq 0 -or $descriptor.pid -ne $ExceptPid)) {
            return $descriptor
        }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    throw "A healthy Agent Farm daemon did not appear within $TimeoutSeconds seconds."
}

function Start-TestDesktop {
    $launch = winapp run $AppOutput --detach --json | Out-String | ConvertFrom-Json
    $pidValue = [int]$launch.ProcessId
    $desktopPids.Add($pidValue)
    winapp ui wait-for TaskPrompt -a $pidValue -t 15000 | Out-Null
    return $pidValue
}

function Close-TestDesktop {
    param([int]$DesktopPid)
    $process = Get-Process -Id $DesktopPid -ErrorAction Stop
    if (-not $process.CloseMainWindow()) {
        throw "Desktop PID $DesktopPid did not accept a close request."
    }
    if (-not $process.WaitForExit(10000)) {
        throw "Desktop PID $DesktopPid did not exit after its window closed."
    }
}

if ($null -ne (Get-HealthyDescriptor)) {
    throw "A healthy Agent Farm daemon is already running for this repository. Stop it before this destructive restart test."
}

$results = [ordered]@{}
try {
    $firstDesktop = Start-TestDesktop
    $firstDaemon = Wait-HealthyDescriptor
    $firstDaemonPid = [int]$firstDaemon.pid
    $ownedDaemonPids.Add($firstDaemonPid) | Out-Null
    $results.initial_daemon_pid = $firstDaemonPid

    Stop-Process -Id $firstDaemonPid -Force
    $replacement = Wait-HealthyDescriptor -ExceptPid $firstDaemonPid
    $replacementPid = [int]$replacement.pid
    $ownedDaemonPids.Add($replacementPid) | Out-Null
    $results.replacement_daemon_pid = $replacementPid
    $results.crash_reconnected = $replacementPid -ne $firstDaemonPid

    winapp ui wait-for TaskPrompt -a $firstDesktop -t 5000 | Out-Null
    # The descriptor becomes healthy slightly before StartRuntimeAsync finishes
    # reloading bootstrap/settings and detaches its startup Process handle.
    Start-Sleep -Seconds 2
    Close-TestDesktop $firstDesktop
    $desktopPids.Remove($firstDesktop) | Out-Null
    $results.daemon_survived_close = $null -ne (Get-HealthyDescriptor)

    $secondDesktop = Start-TestDesktop
    $reused = Wait-HealthyDescriptor
    $ownedDaemonPids.Add([int]$reused.pid) | Out-Null
    $results.reopen_reused_daemon = [int]$reused.pid -eq $replacementPid
    Close-TestDesktop $secondDesktop
    $desktopPids.Remove($secondDesktop) | Out-Null

    if (-not ($results.crash_reconnected -and $results.daemon_survived_close -and $results.reopen_reused_daemon)) {
        throw "One or more daemon lifecycle assertions failed: $($results | ConvertTo-Json -Compress)"
    }

    $results.status = "PASS"
    $results | ConvertTo-Json
}
finally {
    foreach ($desktopPid in @($desktopPids)) {
        $process = Get-Process -Id $desktopPid -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            $null = $process.CloseMainWindow()
            $null = $process.WaitForExit(3000)
        }
    }
    $descriptor = Get-HealthyDescriptor
    if ($null -ne $descriptor -and $ownedDaemonPids.Contains([int]$descriptor.pid)) {
        $authority = ([Uri]$descriptor.url).GetLeftPart([UriPartial]::Authority)
        try {
            Invoke-RestMethod `
                -Uri "$authority/api/runtime/stop" `
                -Method Post `
                -ContentType "application/json" `
                -Body "{}" `
                -TimeoutSec 3 | Out-Null
        }
        catch {
            Stop-Process -Id ([int]$descriptor.pid) -Force -ErrorAction SilentlyContinue
        }
    }
}
