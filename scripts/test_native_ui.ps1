param(
    [Parameter(Mandatory)]
    [int]$AppPid
)

$ErrorActionPreference = "Continue"
$pass = 0
$fail = 0
$results = @()
$artifactRoot = Join-Path $PSScriptRoot "..\test-artifacts\native-ui-tests"
New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null

function Test-UI {
    param([string]$Name, [scriptblock]$Script)
    try {
        $output = & $Script 2>&1
        if ($LASTEXITCODE -eq 0) {
            $script:pass++
            $script:results += @{ name = $Name; status = "PASS" }
        }
        else {
            throw ($output -join "`n")
        }
    }
    catch {
        $script:fail++
        $script:results += @{ name = $Name; status = "FAIL"; detail = "$_" }
    }
}

function Ensure-ExecutionPaneExpanded {
    winapp ui wait-for ExecutionSelector -a $AppPid -t 1000 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        winapp ui invoke ToggleExecutionPaneButton -a $AppPid | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "The compact-layout execution pane could not be expanded."
        }
    }
    winapp ui wait-for ExecutionSelector -a $AppPid -t 5000
}

Test-UI "Native repository picker exists" {
    # A cold Debug launch may spend several seconds starting the Python daemon.
    winapp ui wait-for RepositoryButton -a $AppPid -t 15000
}
Test-UI "Native task composer exists" {
    winapp ui wait-for TaskPrompt -a $AppPid -t 5000
}
Test-UI "Native attachment picker entry point exists" {
    winapp ui wait-for AttachFilesButton -a $AppPid -t 5000
}
Test-UI "Execution pane switches between plan and live output" {
    Ensure-ExecutionPaneExpanded
    winapp ui invoke LiveSelectorItem -a $AppPid
    winapp ui wait-for LiveOutputList -a $AppPid -t 5000
    winapp ui wait-for LiveOutputEmptyState -a $AppPid --value "Live model output will appear when planning or execution starts." -t 5000
    winapp ui invoke PlanSelectorItem -a $AppPid
    winapp ui wait-for WorkerPlanList -a $AppPid -t 5000
}
Test-UI "Task composer accepts text" {
    winapp ui set-value TaskPrompt "Native WinUI smoke test" -a $AppPid
    winapp ui wait-for TaskPrompt -a $AppPid --value "Native WinUI smoke test" -t 2000
}
Test-UI "Task options flyout works" {
    winapp ui invoke TaskOptionsButton -a $AppPid
    winapp ui wait-for WorkerCountBox -a $AppPid -t 5000
    winapp ui wait-for BaseRefBox -a $AppPid -t 5000
    winapp ui focus TaskPrompt -a $AppPid
}
Test-UI "Settings navigation works" {
    winapp ui invoke SettingsNavigationButton -a $AppPid
    winapp ui wait-for SupervisorProviderCombo -a $AppPid -t 5000
    winapp ui wait-for SettingsBackButton -a $AppPid -t 5000
}
Test-UI "Provider settings navigation works" {
    winapp ui invoke ProvidersSettingsButton -a $AppPid
    winapp ui wait-for ProviderTemplateCombo -a $AppPid -t 5000
    winapp ui wait-for ProviderApiKeyBox -a $AppPid -t 5000
}
Test-UI "Runs navigation works" {
    winapp ui invoke RunsNavigationButton -a $AppPid
    winapp ui wait-for RunsViewTitle -a $AppPid --value "Run history" -t 5000
}
Test-UI "Settings back returns to the originating surface" {
    winapp ui invoke SettingsNavigationButton -a $AppPid
    winapp ui wait-for SettingsBackButton -a $AppPid -t 5000
    winapp ui invoke SettingsBackButton -a $AppPid
    winapp ui wait-for RunsViewTitle -a $AppPid --value "Run history" -t 5000
}
Test-UI "Workspace navigation works" {
    winapp ui invoke WorkspaceNavigationButton -a $AppPid
    winapp ui wait-for PlanButton -a $AppPid -t 5000
}
Test-UI "Notification queue opens with an explicit empty state" {
    winapp ui invoke NotificationCenterButton -a $AppPid
    winapp ui wait-for NotificationQueue -a $AppPid -t 5000
    winapp ui wait-for NotificationEmptyState -a $AppPid --value "No notifications yet." -t 5000
    winapp ui focus TaskPrompt -a $AppPid
}
Test-UI "Global keyboard shortcuts move focus predictably" {
    winapp ui focus TaskPrompt -a $AppPid
    winapp ui send-keys ctrl+f -a $AppPid --via send-input
    winapp ui wait-for ThreadSearchBox -a $AppPid -p HasKeyboardFocus --value True -t 5000
    winapp ui send-keys ctrl+n -a $AppPid --via send-input
    winapp ui wait-for TaskPrompt -a $AppPid -p HasKeyboardFocus --value True -t 5000
}
Test-UI "Settings exports a real sanitized diagnostic bundle" {
    winapp ui invoke SettingsNavigationButton -a $AppPid
    winapp ui wait-for ExportDiagnosticsButton -a $AppPid -t 5000
    winapp ui invoke ExportDiagnosticsButton -a $AppPid
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $property = winapp ui get-property DiagnosticBundlePath -a $AppPid --json 2>$null | ConvertFrom-Json
        $serialized = $property | ConvertTo-Json -Compress -Depth 5
    } while ($serialized -notmatch '\.zip' -and (Get-Date) -lt $deadline)
    if ($serialized -notmatch '\.zip') {
        throw "The Settings surface did not report an exported diagnostic ZIP."
    }
    $match = [regex]::Match($serialized, '[A-Za-z]:\\[^\"}]+\.zip')
    if ($match.Success -and -not (Test-Path -LiteralPath $match.Value)) {
        throw "The reported diagnostic ZIP does not exist: $($match.Value)"
    }
}
Test-UI "Settings exposes release channels and verified update actions" {
    winapp ui wait-for ReleaseChannelCombo -a $AppPid -t 5000
    winapp ui wait-for CheckUpdatesButton -a $AppPid -t 5000
    winapp ui wait-for InstallUpdateButton -a $AppPid -t 5000
    winapp ui wait-for UpdateStatusText -a $AppPid -t 5000
}
Test-UI "Desktop source contains no WebView host" {
    $source = Get-Content (Join-Path $PSScriptRoot "..\AgentFarm.Desktop\MainPage.xaml"), (Join-Path $PSScriptRoot "..\AgentFarm.Desktop\MainPage.xaml.cs") -Raw
    if ($source -match "WebView2|CoreWebView2|AgentWebView") {
        throw "A browser host remains in the desktop workspace."
    }
}

winapp ui screenshot -a $AppPid -o (Join-Path $artifactRoot "workspace.png") 2>$null
winapp ui invoke SettingsNavigationButton -a $AppPid 2>$null
winapp ui screenshot -a $AppPid -o (Join-Path $artifactRoot "settings.png") 2>$null
winapp ui invoke ProvidersSettingsButton -a $AppPid 2>$null
winapp ui screenshot -a $AppPid -o (Join-Path $artifactRoot "providers.png") 2>$null

Write-Host "Passed: $pass | Failed: $fail"
$results | Where-Object { $_.status -eq "FAIL" } | ForEach-Object {
    Write-Host "  FAIL: $($_.name) - $($_.detail)" -ForegroundColor Red
    if ($env:GITHUB_ACTIONS -eq "true") {
        $title = "Native UI: $($_.name)".Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
        $detail = "$($_.detail)".Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
        Write-Output "::error title=$title::$detail"
    }
}
$results | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $artifactRoot "results.json")
if ($fail -gt 0) { exit 1 }
