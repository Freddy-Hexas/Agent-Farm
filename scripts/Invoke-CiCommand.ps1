param(
    [Parameter(Mandatory = $true)]
    [string]$Title,
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [string[]]$Arguments = @()
)

$captured = [Collections.Generic.List[string]]::new()
& $Executable @Arguments 2>&1 | ForEach-Object {
    $line = $_.ToString()
    Write-Host $line
    $captured.Add($line)
}
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    $tail = (($captured | Select-Object -Last 80) -join "`n")
    $escaped = $tail.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
    Write-Output "::error title=$Title::$escaped"
    exit $exitCode
}
