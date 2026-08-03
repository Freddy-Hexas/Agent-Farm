param(
    [Parameter(Mandatory)]
    [string]$ReleaseOutput
)

$ErrorActionPreference = "Stop"
$resolvedOutput = (Resolve-Path -LiteralPath $ReleaseOutput).Path
$backend = Join-Path $resolvedOutput "Backend\AgentFarmBackend.exe"
if (-not (Test-Path -LiteralPath $backend)) { throw "Frozen backend is missing: $backend" }

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) "agent-farm-frozen-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
$process = $null
try {
    git init $temporaryRoot | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not initialize the frozen-backend smoke repository." }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $backend
    $startInfo.WorkingDirectory = $temporaryRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.Arguments = "--repo `"$temporaryRoot`" --daemon"
    $process = [Diagnostics.Process]::Start($startInfo)

    $descriptorPath = Join-Path $temporaryRoot ".agent-farm\runtime.json"
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $descriptor = $null
    while ([DateTime]::UtcNow -lt $deadline -and -not $process.HasExited) {
        Start-Sleep -Milliseconds 100
        if (Test-Path -LiteralPath $descriptorPath) {
            try {
                $candidate = Get-Content -LiteralPath $descriptorPath -Raw | ConvertFrom-Json
                if ([int]$candidate.pid -eq $process.Id) { $descriptor = $candidate; break }
            }
            catch { }
        }
    }
    if ($null -eq $descriptor) {
        $logPath = Join-Path $temporaryRoot ".agent-farm\logs\runtime.log"
        $details = if (Test-Path -LiteralPath $logPath) { Get-Content -LiteralPath $logPath -Raw } else { "No runtime log." }
        $exitDescription = if ($process.HasExited) { $process.ExitCode } else { "still running" }
        throw "Frozen backend did not become healthy. Exit=$exitDescription.`n$details"
    }

    $baseUri = ([Uri]$descriptor.url).GetLeftPart([UriPartial]::Authority)
    $health = Invoke-RestMethod "$baseUri/api/health"
    if ($health.status -ne "ok" -or [int]$health.protocol_version -ne 1) {
        throw "Frozen backend returned an incompatible health response."
    }
    $bundle = Invoke-RestMethod "$baseUri/api/diagnostics/export" `
        -Method Post `
        -Headers @{ "Content-Type" = "application/json"; "X-Correlation-ID" = "frozen-release-smoke" } `
        -Body "{}"
    if (-not (Test-Path -LiteralPath $bundle.path)) {
        throw "Frozen backend diagnostic export did not produce a file."
    }
    $stop = Invoke-RestMethod "$baseUri/api/runtime/stop" `
        -Method Post `
        -ContentType "application/json" `
        -Body "{}"
    if ($stop.status -ne "stopping") { throw "Frozen backend did not accept graceful shutdown." }
    if (-not $process.WaitForExit(10000) -or $process.ExitCode -ne 0) {
        throw "Frozen backend did not stop cleanly."
    }

    [pscustomobject]@{
        status = "passed"
        protocol_version = [int]$health.protocol_version
        diagnostic_export = $true
    } | ConvertTo-Json
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        $process.Kill($true)
        $process.WaitForExit(3000) | Out-Null
    }
    $resolvedTemporary = [IO.Path]::GetFullPath($temporaryRoot)
    $systemTemporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolvedTemporary.StartsWith($systemTemporary, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}
