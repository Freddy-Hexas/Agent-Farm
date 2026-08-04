using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace AgentFarm_Desktop.ViewModels;

public enum ShellSurface
{
    Workspace,
    Runs,
    Settings,
}

public enum ExecutionLifecycle
{
    Idle,
    Planning,
    PlanReady,
    Starting,
    Running,
    Cancelling,
    Completed,
    Failed,
}

public enum SettingsSection
{
    AgentRoutes,
    Providers,
}

public enum RuntimeWorkspaceState
{
    Loading,
    Ready,
    Degraded,
    Offline,
    Recovering,
}

public partial class RuntimeStateViewModel : ObservableObject
{
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsLoading))]
    [NotifyPropertyChangedFor(nameof(IsBannerVisible))]
    [NotifyPropertyChangedFor(nameof(IsBusy))]
    [NotifyPropertyChangedFor(nameof(CanRecover))]
    public partial RuntimeWorkspaceState State { get; set; } = RuntimeWorkspaceState.Loading;

    [ObservableProperty]
    public partial string Title { get; set; } = "Starting Agent Farm";

    [ObservableProperty]
    public partial string Message { get; set; } = "Connecting to the local runtime.";

    public bool IsLoading => State == RuntimeWorkspaceState.Loading;
    public bool IsBannerVisible => State is RuntimeWorkspaceState.Degraded or RuntimeWorkspaceState.Offline or RuntimeWorkspaceState.Recovering;
    public bool IsBusy => State is RuntimeWorkspaceState.Loading or RuntimeWorkspaceState.Recovering;
    public bool CanRecover => State is RuntimeWorkspaceState.Degraded or RuntimeWorkspaceState.Offline;

    public void SetLoading(string message = "Connecting to the local runtime.") =>
        Set(RuntimeWorkspaceState.Loading, "Starting Agent Farm", message);

    public void SetReady() =>
        Set(RuntimeWorkspaceState.Ready, "Agent Farm is ready", "The local runtime is connected.");

    public void SetDegraded(string message) =>
        Set(RuntimeWorkspaceState.Degraded, "Runtime is degraded", message);

    public void SetOffline(string message) =>
        Set(RuntimeWorkspaceState.Offline, "Runtime is offline", message);

    public void SetRecovering(string message) =>
        Set(RuntimeWorkspaceState.Recovering, "Reconnecting to Agent Farm", message);

    private void Set(RuntimeWorkspaceState state, string title, string message)
    {
        Title = title;
        Message = message;
        State = state;
    }
}

public partial class ShellViewModel : ObservableObject
{
    private ShellSurface _surfaceBeforeSettings = ShellSurface.Workspace;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsWorkspaceVisible))]
    [NotifyPropertyChangedFor(nameof(IsRunsVisible))]
    [NotifyPropertyChangedFor(nameof(IsSettingsVisible))]
    public partial ShellSurface ActiveSurface { get; set; } = ShellSurface.Workspace;

    [ObservableProperty]
    public partial bool IsNavigationPaneCollapsed { get; set; }

    [ObservableProperty]
    public partial bool IsExecutionPaneCollapsed { get; set; }

    public bool IsWorkspaceVisible => ActiveSurface == ShellSurface.Workspace;
    public bool IsRunsVisible => ActiveSurface == ShellSurface.Runs;
    public bool IsSettingsVisible => ActiveSurface == ShellSurface.Settings;

    [RelayCommand]
    private void ShowWorkspace() => NavigateTo(ShellSurface.Workspace);

    [RelayCommand]
    private void ShowRuns() => NavigateTo(ShellSurface.Runs);

    [RelayCommand]
    private void ShowSettings()
    {
        if (ActiveSurface != ShellSurface.Settings)
        {
            _surfaceBeforeSettings = ActiveSurface;
        }

        ActiveSurface = ShellSurface.Settings;
    }

    [RelayCommand]
    private void ReturnFromSettings()
    {
        if (ActiveSurface != ShellSurface.Settings)
        {
            return;
        }

        ActiveSurface = _surfaceBeforeSettings == ShellSurface.Settings
            ? ShellSurface.Workspace
            : _surfaceBeforeSettings;
    }

    private void NavigateTo(ShellSurface surface)
    {
        ActiveSurface = surface;
    }

    [RelayCommand]
    private void ToggleNavigationPane() => IsNavigationPaneCollapsed = !IsNavigationPaneCollapsed;

    [RelayCommand]
    private void ToggleExecutionPane() => IsExecutionPaneCollapsed = !IsExecutionPaneCollapsed;
}

public partial class ExecutionViewModel : ObservableObject
{
    [ObservableProperty]
    public partial ExecutionLifecycle Lifecycle { get; set; } = ExecutionLifecycle.Idle;

    [ObservableProperty]
    public partial string PlanState { get; set; } = "Waiting for task";

    [ObservableProperty]
    public partial bool CanStart { get; set; }

    [ObservableProperty]
    public partial bool CanCancel { get; set; }

    [ObservableProperty]
    public partial string StartLabel { get; set; } = "Start execution";

    [ObservableProperty]
    public partial string CancelAutomationName { get; set; } = "Cancel active run";

    public event EventHandler? StartRequested;
    public event EventHandler? CancelRequested;

    [RelayCommand(CanExecute = nameof(CanRequestStart))]
    private void RequestStart() => StartRequested?.Invoke(this, EventArgs.Empty);

    [RelayCommand(CanExecute = nameof(CanRequestCancel))]
    private void RequestCancel() => CancelRequested?.Invoke(this, EventArgs.Empty);

    private bool CanRequestStart() => CanStart;
    private bool CanRequestCancel() => CanCancel;

    partial void OnCanStartChanged(bool value) => RequestStartCommand.NotifyCanExecuteChanged();
    partial void OnCanCancelChanged(bool value) => RequestCancelCommand.NotifyCanExecuteChanged();

    public void Reset()
    {
        Lifecycle = ExecutionLifecycle.Idle;
        PlanState = "Waiting for task";
        CanStart = false;
        CanCancel = false;
        StartLabel = "Start execution";
        CancelAutomationName = "Cancel active run";
    }

    public void BeginPlanning(string state = "Supervisor planning")
    {
        Lifecycle = ExecutionLifecycle.Planning;
        PlanState = state;
        CanStart = false;
        CanCancel = true;
        CancelAutomationName = "Cancel planning";
    }

    public void SetQueued(string state)
    {
        Lifecycle = ExecutionLifecycle.Planning;
        PlanState = state;
        CanCancel = true;
    }

    public void SetPlanReady(int workerCount)
    {
        Lifecycle = ExecutionLifecycle.PlanReady;
        PlanState = $"{workerCount} workers ready";
        CanStart = workerCount > 0;
        CanCancel = false;
        StartLabel = "Start execution";
    }

    public void InvalidatePlan(string reason)
    {
        Lifecycle = ExecutionLifecycle.Idle;
        PlanState = reason;
        CanStart = false;
    }

    public void BeginFarmStart()
    {
        Lifecycle = ExecutionLifecycle.Starting;
        PlanState = "Starting workers";
        CanStart = false;
        CanCancel = true;
        StartLabel = "Starting…";
        CancelAutomationName = "Cancel Farm execution";
    }

    public void MarkRunning(string state = "Workers running")
    {
        Lifecycle = ExecutionLifecycle.Running;
        PlanState = state;
        CanStart = false;
        CanCancel = true;
    }

    public void MarkCompleted()
    {
        Lifecycle = ExecutionLifecycle.Completed;
        PlanState = "Execution completed";
        CanStart = false;
        CanCancel = false;
        StartLabel = "Start execution";
    }

    public void BeginCancelling(string state)
    {
        Lifecycle = ExecutionLifecycle.Cancelling;
        PlanState = state;
        CanCancel = false;
    }

    public void Recover(bool hasPlan, bool hasActiveJob, string state)
    {
        Lifecycle = hasActiveJob ? ExecutionLifecycle.Running : hasPlan ? ExecutionLifecycle.PlanReady : ExecutionLifecycle.Idle;
        PlanState = state;
        CanStart = hasPlan && !hasActiveJob;
        CanCancel = hasActiveJob;
        StartLabel = "Start execution";
    }

    public void MarkFailed(string state, bool canRetry)
    {
        Lifecycle = ExecutionLifecycle.Failed;
        PlanState = state;
        CanStart = canRetry;
        CanCancel = false;
        StartLabel = canRetry ? "Retry execution" : "Start execution";
    }
}

public partial class ReviewViewModel : ObservableObject
{
    [ObservableProperty]
    public partial string State { get; set; } = "Select a run";

    [ObservableProperty]
    public partial string Decision { get; set; } = "Supervisor decision and evidence will appear here.";

    [ObservableProperty]
    public partial string Cost { get; set; } = "Supervisor: no usage yet | Workers: no usage yet";

    [ObservableProperty]
    public partial bool CanApply { get; set; }

    [ObservableProperty]
    public partial bool CanMerge { get; set; }

    [ObservableProperty]
    public partial bool CanRollback { get; set; }

    public event EventHandler? ApplyRequested;
    public event EventHandler? MergeRequested;
    public event EventHandler? RollbackRequested;

    [RelayCommand(CanExecute = nameof(CanRequestApply))]
    private void RequestApply() => ApplyRequested?.Invoke(this, EventArgs.Empty);

    [RelayCommand(CanExecute = nameof(CanRequestMerge))]
    private void RequestMerge() => MergeRequested?.Invoke(this, EventArgs.Empty);

    [RelayCommand(CanExecute = nameof(CanRequestRollback))]
    private void RequestRollback() => RollbackRequested?.Invoke(this, EventArgs.Empty);

    private bool CanRequestApply() => CanApply;
    private bool CanRequestMerge() => CanMerge;
    private bool CanRequestRollback() => CanRollback;

    partial void OnCanApplyChanged(bool value) => RequestApplyCommand.NotifyCanExecuteChanged();
    partial void OnCanMergeChanged(bool value) => RequestMergeCommand.NotifyCanExecuteChanged();
    partial void OnCanRollbackChanged(bool value) => RequestRollbackCommand.NotifyCanExecuteChanged();

    public void ResetActions()
    {
        CanApply = false;
        CanMerge = false;
        CanRollback = false;
    }
}

public partial class SettingsViewModel : ObservableObject
{
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsAgentRoutesVisible))]
    [NotifyPropertyChangedFor(nameof(IsProvidersVisible))]
    public partial SettingsSection ActiveSection { get; set; } = SettingsSection.AgentRoutes;

    public bool IsAgentRoutesVisible => ActiveSection == SettingsSection.AgentRoutes;
    public bool IsProvidersVisible => ActiveSection == SettingsSection.Providers;

    [RelayCommand]
    private void ShowAgentRoutes() => ActiveSection = SettingsSection.AgentRoutes;

    [RelayCommand]
    private void ShowProviders() => ActiveSection = SettingsSection.Providers;
}
