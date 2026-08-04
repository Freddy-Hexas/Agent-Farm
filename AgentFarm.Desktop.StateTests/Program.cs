using AgentFarm.Core;
using AgentFarm_Desktop.ViewModels;

static void Assert(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

var shell = new ShellViewModel();
Assert(shell.IsWorkspaceVisible, "Workspace must be the initial surface.");
shell.ShowRunsCommand.Execute(null);
Assert(shell.ActiveSurface == ShellSurface.Runs && shell.IsRunsVisible, "Runs command must switch the shell.");
shell.ShowSettingsCommand.Execute(null);
Assert(shell.IsSettingsVisible && !shell.IsWorkspaceVisible, "Settings must hide the workspace.");
shell.ReturnFromSettingsCommand.Execute(null);
Assert(shell.ActiveSurface == ShellSurface.Runs, "Settings back must return to the originating Runs surface.");
shell.ShowWorkspaceCommand.Execute(null);
shell.ShowSettingsCommand.Execute(null);
shell.ReturnFromSettingsCommand.Execute(null);
Assert(shell.ActiveSurface == ShellSurface.Workspace, "Settings back must return to the originating Workspace surface.");
shell.ToggleNavigationPaneCommand.Execute(null);
Assert(shell.IsNavigationPaneCollapsed, "Navigation toggle must update shell state.");

var execution = new ExecutionViewModel();
var startRequests = 0;
var cancelRequests = 0;
execution.StartRequested += (_, _) => startRequests++;
execution.CancelRequested += (_, _) => cancelRequests++;
Assert(!execution.RequestStartCommand.CanExecute(null), "Idle execution must not start.");
execution.BeginPlanning();
Assert(execution.Lifecycle == ExecutionLifecycle.Planning, "Planning transition was not recorded.");
Assert(execution.CanCancel && execution.RequestCancelCommand.CanExecute(null), "Planning must be cancellable.");
execution.RequestCancelCommand.Execute(null);
Assert(cancelRequests == 1, "Cancel command must emit exactly one request.");
execution.SetPlanReady(2);
Assert(execution.CanStart && execution.PlanState == "2 workers ready", "Ready plan state is inconsistent.");
execution.RequestStartCommand.Execute(null);
Assert(startRequests == 1, "Start command must emit exactly one request.");
execution.BeginFarmStart();
Assert(execution.Lifecycle == ExecutionLifecycle.Starting && execution.CanCancel, "Farm start transition is inconsistent.");
execution.MarkRunning();
execution.BeginCancelling("Cancelling Farm");
Assert(execution.Lifecycle == ExecutionLifecycle.Cancelling && !execution.CanCancel, "Cancel transition is inconsistent.");
execution.MarkCompleted();
Assert(execution.Lifecycle == ExecutionLifecycle.Completed && !execution.CanStart, "Completed state must be terminal.");
execution.MarkFailed("Execution failed", canRetry: true);
Assert(execution.RequestStartCommand.CanExecute(null) && execution.StartLabel == "Retry execution", "Failed execution must expose retry when allowed.");
execution.Reset();
Assert(execution.Lifecycle == ExecutionLifecycle.Idle && !execution.CanStart && !execution.CanCancel, "Reset must clear execution controls.");

var review = new ReviewViewModel();
var applyRequests = 0;
review.ApplyRequested += (_, _) => applyRequests++;
Assert(!review.RequestApplyCommand.CanExecute(null), "Review apply must be gated initially.");
review.CanApply = true;
review.RequestApplyCommand.Execute(null);
Assert(applyRequests == 1, "Review apply command must emit one request.");
review.ResetActions();
Assert(!review.CanApply && !review.CanMerge && !review.CanRollback, "Review reset must gate all actions.");

var settings = new SettingsViewModel();
Assert(settings.IsAgentRoutesVisible, "Agent routes must be the initial settings section.");
settings.ShowProvidersCommand.Execute(null);
Assert(settings.IsProvidersVisible && !settings.IsAgentRoutesVisible, "Provider command must switch settings sections.");

var runtime = new RuntimeStateViewModel();
Assert(runtime.IsLoading && runtime.IsBusy && !runtime.IsBannerVisible, "Runtime must begin in a loading state.");
runtime.SetReady();
Assert(runtime.State == RuntimeWorkspaceState.Ready && !runtime.IsBusy && !runtime.IsBannerVisible, "Ready runtime state is inconsistent.");
runtime.SetDegraded("A capability is unavailable.");
Assert(runtime.IsBannerVisible && runtime.CanRecover, "Degraded runtime must expose recovery.");
runtime.SetRecovering("Restoring the local connection.");
Assert(runtime.IsBusy && runtime.IsBannerVisible && !runtime.CanRecover, "Recovery must be visible and prevent duplicate reconnects.");
runtime.SetOffline("The local runtime is unavailable.");
Assert(runtime.CanRecover && runtime.Title == "Runtime is offline", "Offline runtime must expose a retry action.");

Assert(UpdatePolicy.ParseVersion("v1.2.3") == new Version(1, 2, 3, 0), "Three-part release versions must normalize to four parts.");
Assert(UpdatePolicy.ParseVersion("Agent Farm 2.4.6.8 Preview") == new Version(2, 4, 6, 8), "Release titles must expose four-part versions.");
Assert(UpdatePolicy.ParseVersion("preview") is null, "Invalid release names must not produce versions.");
Assert(UpdatePolicy.IsApprovedReleaseUri("https://github.com/Freddy-Hexas/Agent-Farm/releases/download/v1.0.0/app.msix"), "GitHub release assets must be approved.");
Assert(UpdatePolicy.IsApprovedReleaseUri("https://objects.githubusercontent.com/release.msix"), "GitHub content delivery must be approved.");
Assert(!UpdatePolicy.IsApprovedReleaseUri("http://github.com/release.msix"), "Update assets must require HTTPS.");
Assert(!UpdatePolicy.IsApprovedReleaseUri("https://example.com/release.msix"), "Unapproved update origins must be rejected.");
Assert(UpdatePolicy.AutomaticCheckCadence("stable") == TimeSpan.FromHours(24), "Stable update cadence is inconsistent.");
Assert(UpdatePolicy.AutomaticCheckCadence("preview") == TimeSpan.FromHours(4), "Preview update cadence is inconsistent.");

Console.WriteLine("Agent Farm MVVM state tests passed.");
