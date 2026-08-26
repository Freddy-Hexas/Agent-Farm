using AgentFarm.Core;
using AgentFarm_Desktop.Models;
using AgentFarm_Desktop.Services;
using AgentFarm_Desktop.ViewModels;
using AgentFarm_Desktop.Views;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using System.ComponentModel;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Windows.Storage.Pickers;
using Windows.Storage;

namespace AgentFarm_Desktop;

public sealed partial class MainPage : Page
{
    private static readonly string[] RequiredProtocolCapabilities =
    [
        "approvals.v1",
        "cancellation.v1",
        "durable-jobs.v1",
        "model-deltas.v1",
        "reconnect-cursor.v1",
        "typed-messages.v1",
    ];
    private const string LeftPaneWidthKey = "layout.leftPaneWidth";
    private const string RightPaneWidthKey = "layout.rightPaneWidth";
    private const string UpdateChannelKey = "updates.channel";
    private const string LastUpdateCheckPrefix = "updates.lastCheck.";
    private const double DefaultLeftPaneWidth = 248;
    private const double DefaultRightPaneWidth = 328;
    private const double NavigationCollapseBreakpoint = 820;
    private const double ExecutionCollapseBreakpoint = 1120;
    private static readonly Regex ProviderIdPattern = new("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", RegexOptions.CultureInvariant);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true,
        WriteIndented = true,
    };

    private readonly AgentRuntimeProcess _runtime = new();
    private readonly CancellationTokenSource _lifetime = new();
    private readonly SemaphoreSlim _runtimeGate = new(1, 1);
    private readonly NotificationService _notifications;
    private readonly UpdateService _updates = new();
    private AgentFarmApiClient? _api;
    private Task? _healthMonitorTask;
    private SettingsResponse? _settings;
    private BootstrapResponse? _bootstrap;
    private WorkerPlan? _currentPlan;
    private string? _currentThreadId;
    private string? _currentTurnId;
    private string? _currentPlanJobId;
    private string? _currentFarmJobId;
    private string? _lastFarmJobId;
    private string? _currentReviewFarmId;
    private JsonObject? _currentReviewResult;
    private UpdateCheckResult? _availableUpdate;
    private WorkerProfileEditor? _selectedWorkerProfile;
    private ProviderEditor? _selectedProvider;
    private bool _started;
    private bool _settingsUiUpdating;
    private bool _paneWidthsReady;
    private bool _leftPaneCollapsed;
    private bool _rightPaneCollapsed;
    private bool _leftPaneAutoCollapsed;
    private bool _rightPaneAutoCollapsed;
    private bool _planning;

    private readonly record struct ProviderRename(string OldId, string NewId);
    private string? _reportedRecoverySessionId;
    private int _focusRegionIndex;

    public MainPageViewModel ViewModel { get; } = new();

    public MainPage()
    {
        App.WriteMarker("MainPage constructor entered");
        InitializeComponent();
        App.WriteMarker("MainPage XAML initialized");
        _notifications = new NotificationService(ViewModel.Notifications);
        WorkspaceSurface.ComposerView.PlanRequested += OnComposerPlanRequested;
        WorkspaceSurface.ComposerView.AttachFilesRequested += OnComposerAttachFilesRequested;
        WorkspaceSurface.ComposerView.ConfigureRoutingRequested += OnComposerConfigureRoutingRequested;
        WorkspaceSurface.ComposerView.FilesDropped += OnComposerFilesDropped;
        WorkspaceSurface.ComposerView.AttachmentRemovalRequested += OnComposerAttachmentRemovalRequested;
        WorkspaceSurface.ComposerView.DropFailed += OnComposerDropFailed;
        ViewModel.Execution.StartRequested += OnExecutionStartRequested;
        ViewModel.Execution.CancelRequested += OnExecutionCancelRequested;
        WorkspaceSurface.ExecutionView.ApprovalRequested += OnExecutionApprovalRequested;
        WorkspaceSurface.ExecutionView.WorkerCancelRequested += OnExecutionWorkerCancelRequested;
        WorkspaceSurface.ExecutionView.WorkerRetryRequested += OnExecutionWorkerRetryRequested;
        WorkspaceSurface.RefreshRequested += (sender, args) => OnRefresh(sender!, new RoutedEventArgs());
        WorkspaceSurface.RuntimeRecoveryRequested += OnRuntimeRecoveryRequested;
        WorkspaceSurface.ExecutionPaneSizeChanged += (sender, args) => OnRightPaneSizeChanged(sender!, args);
        WorkspaceSurface.ExecutionSplitterDoubleTapped += (sender, args) => OnRightPaneSplitterDoubleTapped(sender!, args);
        ViewModel.Shell.PropertyChanged += OnShellPropertyChanged;
        RunsSurface.RunSelected += OnRunSelected;
        RunsSurface.Review.SelectionChanged += OnReviewSelectionChanged;
        ViewModel.Review.ApplyRequested += OnReviewApplyRequested;
        ViewModel.Review.MergeRequested += OnReviewMergeRequested;
        ViewModel.Review.RollbackRequested += OnReviewRollbackRequested;
        SettingsSurface.SaveRequested += (sender, args) => OnSaveSettings(sender!, new RoutedEventArgs());
        SettingsSurface.SupervisorProviderChanged += (sender, args) => OnSupervisorProviderChanged(sender!, args);
        SettingsSurface.SupervisorModelChanged += (sender, args) => OnSupervisorModelChanged(sender!, args);
        SettingsSurface.SupervisorCustomModelChanged += (sender, args) => OnSupervisorCustomModelChanged(sender!, args);
        SettingsSurface.AddWorkerProfileRequested += (sender, args) => OnAddWorkerProfile(sender!, new RoutedEventArgs());
        SettingsSurface.WorkerProfileChanged += (sender, args) => OnWorkerProfileSelectionChanged(sender!, args);
        SettingsSurface.WorkerProviderChanged += (sender, args) => OnWorkerProviderChanged(sender!, args);
        SettingsSurface.WorkerModelChanged += (sender, args) => OnWorkerModelChanged(sender!, args);
        SettingsSurface.WorkerCustomModelChanged += (sender, args) => OnWorkerCustomModelChanged(sender!, args);
        SettingsSurface.RemoveWorkerProfileRequested += (sender, args) => OnRemoveWorkerProfile(sender!, new RoutedEventArgs());
        SettingsSurface.DiagnosticsExportRequested += OnDiagnosticsExportRequested;
        SettingsSurface.ReleaseChannelChanged += OnReleaseChannelChanged;
        SettingsSurface.UpdateCheckRequested += OnUpdateCheckRequested;
        SettingsSurface.UpdateInstallRequested += OnUpdateInstallRequested;
        SettingsSurface.Providers.AddRequested += (sender, args) => OnAddProvider(sender!, new RoutedEventArgs());
        SettingsSurface.Providers.SelectionChanged += (sender, args) => OnProviderSelectionChanged(sender!, EventArgs.Empty);
        SettingsSurface.Providers.WireApiChanged += (sender, args) => OnProviderWireApiChanged(sender!, EventArgs.Empty);
        SettingsSurface.Providers.ApiKeyChanged += (sender, args) => OnProviderApiKeyChanged(sender!, new RoutedEventArgs());
        SettingsSurface.Providers.RefreshRequested += (sender, args) => OnRefreshProviderModels(sender!, new RoutedEventArgs());
        SettingsSurface.Providers.RemoveRequested += (sender, args) => OnRemoveProvider(sender!, new RoutedEventArgs());
        var savedChannel = ApplicationData.Current.LocalSettings.Values[UpdateChannelKey] as string;
        SettingsSurface.ReleaseChannel.SelectedItem = savedChannel is "preview" ? "preview" : "stable";
        RestorePaneWidths();
    }

    private void RestorePaneWidths()
    {
        var values = ApplicationData.Current.LocalSettings.Values;
        LeftPaneColumn.Width = new GridLength(ReadPaneWidth(
            values,
            LeftPaneWidthKey,
            DefaultLeftPaneWidth,
            LeftPaneColumn.MinWidth,
            LeftPaneColumn.MaxWidth));
        WorkspaceSurface.RightPane.Width = new GridLength(ReadPaneWidth(
            values,
            RightPaneWidthKey,
            DefaultRightPaneWidth,
            WorkspaceSurface.RightPane.MinWidth,
            WorkspaceSurface.RightPane.MaxWidth));
        _paneWidthsReady = true;
    }

    private static double ReadPaneWidth(
        IDictionary<string, object> values,
        string key,
        double fallback,
        double minimum,
        double maximum)
    {
        var stored = values.TryGetValue(key, out var value) && value is double width
            ? width
            : fallback;
        return Math.Clamp(stored, minimum, maximum);
    }

    private void OnLeftPaneSizeChanged(object sender, SizeChangedEventArgs e)
    {
        SavePaneWidth(LeftPaneWidthKey, e.NewSize.Width, LeftPaneColumn.MinWidth, LeftPaneColumn.MaxWidth);
    }

    private void OnRightPaneSizeChanged(object sender, SizeChangedEventArgs e)
    {
        SavePaneWidth(RightPaneWidthKey, e.NewSize.Width, WorkspaceSurface.RightPane.MinWidth, WorkspaceSurface.RightPane.MaxWidth);
    }

    private void SavePaneWidth(string key, double width, double minimum, double maximum)
    {
        if (!_paneWidthsReady || width < minimum || width > maximum)
        {
            return;
        }

        ApplicationData.Current.LocalSettings.Values[key] = Math.Round(width, 1);
    }

    private void OnLeftPaneSplitterDoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        LeftPaneColumn.Width = new GridLength(DefaultLeftPaneWidth);
        e.Handled = true;
    }

    private void OnRightPaneSplitterDoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        WorkspaceSurface.RightPane.Width = new GridLength(DefaultRightPaneWidth);
        e.Handled = true;
    }

    private void OnAppShellSizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (!_paneWidthsReady || e.NewSize.Width <= 0)
        {
            return;
        }

        if (e.NewSize.Width < NavigationCollapseBreakpoint)
        {
            SetNavigationPaneCollapsed(true, automatic: true);
        }
        else if (_leftPaneAutoCollapsed)
        {
            SetNavigationPaneCollapsed(false, automatic: true);
        }

        if (e.NewSize.Width < ExecutionCollapseBreakpoint)
        {
            SetExecutionPaneCollapsed(true, automatic: true);
        }
        else if (_rightPaneAutoCollapsed)
        {
            SetExecutionPaneCollapsed(false, automatic: true);
        }
    }

    private void OnToggleNavigationPane(object sender, RoutedEventArgs e) =>
        SetNavigationPaneCollapsed(!_leftPaneCollapsed, automatic: false);

    private void OnToggleExecutionPane(object sender, RoutedEventArgs e) =>
        SetExecutionPaneCollapsed(!_rightPaneCollapsed, automatic: false);

    private void OnShellPropertyChanged(object? sender, PropertyChangedEventArgs args)
    {
        if (args.PropertyName == nameof(ShellViewModel.IsNavigationPaneCollapsed))
        {
            SetNavigationPaneCollapsed(ViewModel.Shell.IsNavigationPaneCollapsed, automatic: false);
        }
        else if (args.PropertyName == nameof(ShellViewModel.IsExecutionPaneCollapsed))
        {
            SetExecutionPaneCollapsed(ViewModel.Shell.IsExecutionPaneCollapsed, automatic: false);
        }
        else if (args.PropertyName == nameof(ShellViewModel.ActiveSurface)
            && ViewModel.Shell.ActiveSurface == ShellSurface.Settings)
        {
            ViewModel.Settings.ShowAgentRoutesCommand.Execute(null);
            DispatcherQueue.TryEnqueue(SettingsSurface.FocusPrimary);
        }
        else if (args.PropertyName == nameof(ShellViewModel.ActiveSurface))
        {
            DispatcherQueue.TryEnqueue(FocusActiveSurface);
        }
    }

    private void FocusActiveSurface()
    {
        switch (ViewModel.Shell.ActiveSurface)
        {
            case ShellSurface.Workspace:
                WorkspaceSurface.ComposerView.FocusPrompt();
                break;
            case ShellSurface.Runs:
                RunsSurface.FocusPrimary();
                break;
            case ShellSurface.Settings:
                SettingsSurface.FocusPrimary();
                break;
        }
    }

    private void OnNewTaskAccelerator(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        OnNewTask(NewTaskButton, new RoutedEventArgs());
        args.Handled = true;
    }

    private void OnSearchAccelerator(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        if (_leftPaneCollapsed)
        {
            SetNavigationPaneCollapsed(collapsed: false, automatic: false);
        }
        ThreadSearchBox.Focus(FocusState.Keyboard);
        args.Handled = true;
    }

    private void OnBackAccelerator(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        if (!ViewModel.Shell.IsSettingsVisible)
        {
            return;
        }

        ViewModel.Shell.ReturnFromSettingsCommand.Execute(null);
        args.Handled = true;
    }

    private void OnNextRegionAccelerator(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        var focusActions = new List<Action>();
        if (!_leftPaneCollapsed)
        {
            focusActions.Add(() => RepositoryButton.Focus(FocusState.Keyboard));
        }
        focusActions.Add(FocusActiveSurface);
        if (ViewModel.Shell.ActiveSurface == ShellSurface.Workspace && !_rightPaneCollapsed)
        {
            focusActions.Add(WorkspaceSurface.ExecutionView.FocusPrimary);
        }
        _focusRegionIndex = (_focusRegionIndex + 1) % focusActions.Count;
        focusActions[_focusRegionIndex]();
        args.Handled = true;
    }

    private void SetNavigationPaneCollapsed(bool collapsed, bool automatic)
    {
        if (_leftPaneCollapsed == collapsed)
        {
            if (collapsed && automatic)
            {
                _leftPaneAutoCollapsed = true;
            }
            if (ViewModel.Shell.IsNavigationPaneCollapsed != collapsed)
            {
                ViewModel.Shell.IsNavigationPaneCollapsed = collapsed;
            }
            return;
        }

        _leftPaneCollapsed = collapsed;
        _leftPaneAutoCollapsed = collapsed && automatic;
        if (collapsed)
        {
            LeftPane.Visibility = Visibility.Collapsed;
            LeftPaneDivider.Visibility = Visibility.Collapsed;
            LeftPaneSplitter.Visibility = Visibility.Collapsed;
            LeftPaneColumn.MinWidth = 0;
            LeftPaneColumn.Width = new GridLength(0);
            LeftPaneSplitterColumn.Width = new GridLength(0);
            NavigationToggleIcon.Glyph = "\uE76C";
            AutomationProperties.SetName(ToggleNavigationPaneButton, "Expand navigation pane");
        }
        else
        {
            LeftPaneColumn.MinWidth = 190;
            LeftPaneColumn.Width = new GridLength(ReadPaneWidth(
                ApplicationData.Current.LocalSettings.Values,
                LeftPaneWidthKey,
                DefaultLeftPaneWidth,
                190,
                LeftPaneColumn.MaxWidth));
            LeftPaneSplitterColumn.Width = new GridLength(6);
            LeftPane.Visibility = Visibility.Visible;
            LeftPaneDivider.Visibility = Visibility.Visible;
            LeftPaneSplitter.Visibility = Visibility.Visible;
            NavigationToggleIcon.Glyph = "\uE700";
            AutomationProperties.SetName(ToggleNavigationPaneButton, "Collapse navigation pane");
        }
        if (ViewModel.Shell.IsNavigationPaneCollapsed != collapsed)
        {
            ViewModel.Shell.IsNavigationPaneCollapsed = collapsed;
        }
    }

    private void SetExecutionPaneCollapsed(bool collapsed, bool automatic)
    {
        if (_rightPaneCollapsed == collapsed)
        {
            if (collapsed && automatic)
            {
                _rightPaneAutoCollapsed = true;
            }
            if (ViewModel.Shell.IsExecutionPaneCollapsed != collapsed)
            {
                ViewModel.Shell.IsExecutionPaneCollapsed = collapsed;
            }
            return;
        }

        _rightPaneCollapsed = collapsed;
        _rightPaneAutoCollapsed = collapsed && automatic;
        if (collapsed)
        {
            WorkspaceSurface.ExecutionView.Visibility = Visibility.Collapsed;
            WorkspaceSurface.RightDivider.Visibility = Visibility.Collapsed;
            WorkspaceSurface.RightSplitter.Visibility = Visibility.Collapsed;
            WorkspaceSurface.RightPane.MinWidth = 0;
            WorkspaceSurface.RightPane.Width = new GridLength(0);
            WorkspaceSurface.RightSplitterColumn.Width = new GridLength(0);
            WorkspaceSurface.RightPaneToggleIcon.Glyph = "\uE76C";
            AutomationProperties.SetName(WorkspaceSurface.ToggleRightPaneButton, "Expand execution pane");
        }
        else
        {
            WorkspaceSurface.RightPane.MinWidth = 260;
            WorkspaceSurface.RightPane.Width = new GridLength(ReadPaneWidth(
                ApplicationData.Current.LocalSettings.Values,
                RightPaneWidthKey,
                DefaultRightPaneWidth,
                260,
                WorkspaceSurface.RightPane.MaxWidth));
            WorkspaceSurface.RightSplitterColumn.Width = new GridLength(6);
            WorkspaceSurface.ExecutionView.Visibility = Visibility.Visible;
            WorkspaceSurface.RightDivider.Visibility = Visibility.Visible;
            WorkspaceSurface.RightSplitter.Visibility = Visibility.Visible;
            WorkspaceSurface.RightPaneToggleIcon.Glyph = "\uE76B";
            AutomationProperties.SetName(WorkspaceSurface.ToggleRightPaneButton, "Collapse execution pane");
        }
        if (ViewModel.Shell.IsExecutionPaneCollapsed != collapsed)
        {
            ViewModel.Shell.IsExecutionPaneCollapsed = collapsed;
        }
    }

    public void StopRuntime()
    {
        _lifetime.Cancel();
        _api?.Dispose();
        _api = null;
        _runtime.Dispose();
        _updates.Dispose();
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_started)
        {
            return;
        }

        _started = true;
        await StartRuntimeAsync();
        if (_api is not null)
        {
            _healthMonitorTask = MonitorRuntimeAsync();
        }
    }

    private async Task StartRuntimeAsync(string? repositoryRoot = null, bool isRecovery = false)
    {
        var lockTaken = false;
        StartupError.IsOpen = false;
        if (isRecovery)
        {
            ViewModel.RuntimeState.SetRecovering("Restoring the connection to the local runtime.");
        }
        else
        {
            ViewModel.RuntimeState.SetLoading();
        }
        try
        {
            await _runtimeGate.WaitAsync(_lifetime.Token);
            lockTaken = true;
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_lifetime.Token);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));

            var runtimeUri = await _runtime.StartAsync(timeout.Token, repositoryRoot);
            _api?.Dispose();
            _api = new AgentFarmApiClient(runtimeUri);

            await LoadApplicationStateAsync(timeout.Token);
            ShowWorkspace();
            ViewModel.RuntimeState.SetReady();
            _ = CheckForUpdatesAsync(manual: false);
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
            // The window closed while startup was in progress.
        }
        catch (OperationCanceledException)
        {
            ShowStartupError("The local runtime did not become ready within 30 seconds.", degraded: false);
        }
        catch (Exception exception)
        {
            ShowStartupError(exception.Message, degraded: _api is not null);
        }
        finally
        {
            if (lockTaken)
            {
                _runtimeGate.Release();
            }
        }
    }

    private async Task MonitorRuntimeAsync()
    {
        while (!_lifetime.IsCancellationRequested)
        {
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(5), _lifetime.Token);
            }
            catch (OperationCanceledException)
            {
                return;
            }

            var api = _api;
            if (api is null)
            {
                continue;
            }

            try
            {
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_lifetime.Token);
                timeout.CancelAfter(TimeSpan.FromSeconds(3));
                var health = await api.GetHealthAsync(timeout.Token);
                if (health.Status != "ok" || health.ProtocolVersion != 1)
                {
                    throw new InvalidOperationException("The Agent Farm daemon reported an incompatible health state.");
                }
            }
            catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
            {
                return;
            }
            catch (Exception exception)
            {
                if (!ReferenceEquals(api, _api))
                {
                    continue;
                }
                await RecoverRuntimeAsync(exception.Message);
            }
        }
    }

    private async Task RecoverRuntimeAsync(string reason)
    {
        if (_lifetime.IsCancellationRequested)
        {
            return;
        }

        var repositoryRoot = ViewModel.RepositoryPath;
        ViewModel.RuntimeState.SetRecovering(
            $"The local daemon stopped responding. Agent Farm is restoring the connection. {reason}");
        ShowInfo(
            "Runtime reconnecting",
            $"The local daemon stopped responding. Agent Farm is restoring the connection. {reason}",
            InfoBarSeverity.Warning);
        var api = Interlocked.Exchange(ref _api, null);
        api?.Dispose();
        _runtime.Stop();
        await StartRuntimeAsync(repositoryRoot, isRecovery: true);
        if (_api is not null)
        {
            ShowInfo(
                "Runtime reconnected",
                "The workspace was restored from durable runtime state.",
                InfoBarSeverity.Success);
        }
    }

    private async void OnRuntimeRecoveryRequested(object? sender, EventArgs args)
    {
        if (ViewModel.RuntimeState.IsBusy)
        {
            return;
        }
        await RecoverRuntimeAsync("Recovery was requested from the workspace.");
    }

    private async Task LoadApplicationStateAsync(CancellationToken cancellationToken)
    {
        var api = RequireApi();
        var protocol = await api.InitializeProtocolAsync(
            new ProtocolInitializeRequest
            {
                Capabilities = [.. RequiredProtocolCapabilities, "attachments.v1"],
                RequiredCapabilities = [.. RequiredProtocolCapabilities],
            },
            cancellationToken);
        if (protocol.ProtocolVersion != 1 ||
            RequiredProtocolCapabilities.Except(protocol.EnabledCapabilities).Any())
        {
            throw new InvalidOperationException(
                "The local Agent Farm runtime does not support this desktop protocol.");
        }
        var bootstrapTask = api.GetBootstrapAsync(cancellationToken);
        var settingsTask = api.GetSettingsAsync(cancellationToken);
        var approvalsTask = api.GetPendingApprovalsAsync(cancellationToken);
        await Task.WhenAll(bootstrapTask, settingsTask, approvalsTask);

        ApplyBootstrap(await bootstrapTask);
        ApplySettings(await settingsTask);
        MainPageViewModel.Replace(
            ViewModel.PendingApprovals,
            (await approvalsTask).Approvals.Where(approval => approval.Status == "pending"));
    }

    private void ApplyBootstrap(BootstrapResponse bootstrap)
    {
        _bootstrap = bootstrap;
        ViewModel.ApplyBootstrap(bootstrap);
        WorkspaceSurface.ComposerView.MaximumWorkers = Math.Max(1, Math.Min(12, bootstrap.Limits.MaxParallelWorkers));
        if (bootstrap.Recovery is { Detected: true } recovery &&
            recovery.PreviousSessionId != _reportedRecoverySessionId)
        {
            _reportedRecoverySessionId = recovery.PreviousSessionId;
            ShowInfo(
                "Runtime recovered",
                $"{recovery.Message} {recovery.InterruptedJobs} interrupted job(s) were reconciled.",
                InfoBarSeverity.Warning);
        }
    }

    private async void OnDiagnosticsExportRequested(object? sender, EventArgs args)
    {
        try
        {
            var bundle = await RequireApi().ExportDiagnosticsAsync(_lifetime.Token);
            App.AddDesktopDiagnosticsToBundle(bundle.Path);
            ViewModel.DiagnosticBundlePath = bundle.Path;
            ShowInfo(
                "Diagnostics exported",
                $"A sanitized diagnostic bundle was saved to {bundle.Path}",
                InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowInfo("Diagnostic export failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private void OnReleaseChannelChanged(object? sender, SelectionChangedEventArgs args)
    {
        var channel = SelectedUpdateChannel();
        ApplicationData.Current.LocalSettings.Values[UpdateChannelKey] = channel;
        _availableUpdate = null;
        SettingsSurface.InstallUpdate.IsEnabled = false;
        ViewModel.UpdateStatus = $"Using the {channel} release channel.";
    }

    private async void OnUpdateCheckRequested(object? sender, EventArgs args) =>
        await CheckForUpdatesAsync(manual: true);

    private async void OnUpdateInstallRequested(object? sender, EventArgs args)
    {
        if (_availableUpdate is not { IsAvailable: true } update)
        {
            ShowInfo("No update selected", "Check for an available release first.", InfoBarSeverity.Warning);
            return;
        }
        SettingsSurface.InstallUpdate.IsEnabled = false;
        ViewModel.UpdateStatus = $"Downloading Agent Farm {update.AvailableVersion}…";
        try
        {
            var path = await _updates.DownloadAndLaunchAsync(update, _lifetime.Token);
            ViewModel.UpdateStatus = "The verified update is open in Windows App Installer.";
            ShowInfo(
                "Update ready",
                $"SHA256 verification passed. Windows App Installer opened {path}.",
                InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            SettingsSurface.InstallUpdate.IsEnabled = true;
            ViewModel.UpdateStatus = exception.Message;
            ShowInfo("Update failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async Task CheckForUpdatesAsync(bool manual)
    {
        var channel = SelectedUpdateChannel();
        var values = ApplicationData.Current.LocalSettings.Values;
        var lastCheckKey = LastUpdateCheckPrefix + channel;
        if (!manual && values[lastCheckKey] is string saved &&
            DateTimeOffset.TryParse(saved, out var lastCheck) &&
            DateTimeOffset.UtcNow - lastCheck < UpdatePolicy.AutomaticCheckCadence(channel))
        {
            return;
        }
        if (manual)
        {
            ViewModel.UpdateStatus = $"Checking the {channel} channel…";
        }
        try
        {
            var update = await _updates.CheckAsync(channel, _lifetime.Token);
            values[lastCheckKey] = DateTimeOffset.UtcNow.ToString("O");
            ViewModel.UpdateStatus = update.Message;
            _availableUpdate = update.IsAvailable ? update : null;
            SettingsSurface.InstallUpdate.IsEnabled = update.IsAvailable;
            if (update.IsAvailable)
            {
                ShowInfo("Update available", update.Message, InfoBarSeverity.Informational);
            }
            else if (manual)
            {
                ShowInfo("No update available", update.Message, InfoBarSeverity.Success);
            }
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            if (manual)
            {
                ViewModel.UpdateStatus = exception.Message;
                ShowInfo("Update check failed", exception.Message, InfoBarSeverity.Error);
            }
        }
    }

    private string SelectedUpdateChannel() =>
        SettingsSurface.ReleaseChannel.SelectedItem as string == "preview" ? "preview" : "stable";

    private void ApplySettings(SettingsResponse settings)
    {
        var supervisorProvider = GetString(settings.Config, "supervisor_provider")
            ?? GetString(settings.Config, "worker_provider")
            ?? "openai";
        var supervisorModel = GetString(settings.Config, "supervisor_model")
            ?? GetString(settings.Config, "worker_model")
            ?? string.Empty;
        _settingsUiUpdating = true;
        try
        {
            _settings = settings;
            ViewModel.SettingsPath = settings.EditablePath;
            MainPageViewModel.Replace(ViewModel.ProviderTemplates, settings.ProviderTemplates);
            MainPageViewModel.Replace(ViewModel.HarnessOptions, settings.Options.Harnesses);

            ViewModel.Providers.Clear();
            var providerConfigs = settings.Config["model_providers"] as JsonObject ?? new JsonObject();
            foreach (var pair in providerConfigs)
            {
                if (pair.Value is not JsonObject provider)
                {
                    continue;
                }

                var status = settings.ProviderStatus[pair.Key] as JsonObject;
                var credentialConfigured = GetBoolean(status, "credential_configured");
                var needsCredential = GetBoolean(status, "uses_environment_credential");
                var endpointReachable = status?["endpoint_reachable"]?.GetValue<bool?>();
                var statusText = needsCredential && !credentialConfigured
                    ? "Credential missing"
                    : endpointReachable is false
                        ? "Endpoint offline"
                        : "Configured";

                ViewModel.Providers.Add(new ProviderEditor
                {
                    Id = pair.Key,
                    Name = GetString(provider, "name") ?? pair.Key,
                    BaseUrl = GetString(provider, "base_url") ?? string.Empty,
                    EnvKey = GetString(provider, "env_key") ?? string.Empty,
                    WireApi = GetString(provider, "wire_api") ?? "chat",
                    Status = statusText,
                    Raw = (JsonObject)provider.DeepClone(),
                });
            }

            ViewModel.WorkerProfiles.Clear();
            var profiles = settings.Config["worker_profiles"] as JsonObject ?? new JsonObject();
            foreach (var pair in profiles)
            {
                if (pair.Value is not JsonObject profile)
                {
                    continue;
                }

                ViewModel.WorkerProfiles.Add(new WorkerProfileEditor
                {
                    Name = pair.Key,
                    DisplayName = GetString(profile, "display_name") ?? pair.Key,
                    Harness = GetString(profile, "harness") ?? GetString(settings.Config, "worker_harness") ?? GetString(settings.Config, "agent_backend") ?? "native",
                    Provider = GetString(profile, "provider") ?? string.Empty,
                    Model = GetString(profile, "model") ?? string.Empty,
                    ReasoningMode = GetString(profile, "reasoning_mode") ?? string.Empty,
                    ReasoningEffort = GetString(profile, "reasoning_effort") ?? string.Empty,
                    TimeoutSeconds = GetInteger(profile, "timeout_seconds") ?? GetInteger(settings.Config, "timeout_seconds") ?? 1800,
                    BudgetUsd = GetDouble(profile, "budget_usd") ?? 0,
                    CapabilityTier = GetString(profile, "capability_tier") ?? "standard",
                    Raw = (JsonObject)profile.DeepClone(),
                });
            }

            RefreshProviderOptions();
            SettingsSurface.SupervisorProvider.SelectedValue = supervisorProvider;
            SettingsSurface.SupervisorHarness.SelectedValue = GetString(settings.Config, "supervisor_harness")
                ?? GetString(settings.Config, "agent_backend")
                ?? "native";
            SettingsSurface.SupervisorCustomModel.Text = supervisorModel;
            SetReasoningSelection(
                SettingsSurface.SupervisorThinking,
                GetString(settings.Config, "supervisor_reasoning_mode"));
            SetReasoningSelection(
                SettingsSurface.SupervisorEffort,
                GetString(settings.Config, "supervisor_reasoning_effort"));
            var overrides = settings.Config["codex_config_overrides"] as JsonObject;
            SettingsSurface.NetworkAccess.IsChecked = GetBoolean(
                overrides,
                "sandbox_workspace_write.network_access");
            SettingsSurface.FarmBudget.Value = GetDouble(settings.Config, "farm_budget_usd") ?? 0;
            SettingsSurface.MonthlyBudget.Value = GetDouble(settings.Config, "monthly_budget_usd") ?? 0;
            SettingsSurface.BudgetPolicy.SelectedItem = GetString(settings.Config, "budget_policy") ?? "warn";
            SettingsSurface.BudgetWarningRatio.Value = GetDouble(settings.Config, "budget_warning_ratio") ?? 0.8;

            var defaultProfile = GetString(settings.Config, "default_worker_profile");
            SettingsSurface.DefaultWorkerProfile.SelectedValue = defaultProfile;
            if (ViewModel.WorkerProfiles.Count > 0)
            {
                SettingsSurface.WorkerProfiles.SelectedItem = ViewModel.WorkerProfiles.FirstOrDefault(item => item.Name == defaultProfile)
                    ?? ViewModel.WorkerProfiles[0];
            }

            if (ViewModel.Providers.Count > 0)
            {
                SettingsSurface.Providers.SelectedProviderIndex = 0;
            }
            if (ViewModel.ProviderTemplates.Count > 0)
            {
                SettingsSurface.Providers.SelectedTemplate = ViewModel.ProviderTemplates.FirstOrDefault(item => item.Id == "custom-openai-compatible")
                    ?? ViewModel.ProviderTemplates[0];
            }
        }
        finally
        {
            _settingsUiUpdating = false;
        }

        _ = LoadInitialSupervisorRouteAsync(
            supervisorProvider,
            supervisorModel,
            GetString(settings.Config, "supervisor_reasoning_mode"),
            GetString(settings.Config, "supervisor_reasoning_effort"));
    }

    private async Task LoadInitialSupervisorRouteAsync(
        string provider,
        string model,
        string? reasoningMode,
        string? reasoningEffort)
    {
        await LoadSupervisorModelsAsync(provider, model, false);
        SetReasoningSelection(SettingsSurface.SupervisorThinking, reasoningMode);
        SetReasoningSelection(SettingsSurface.SupervisorEffort, reasoningEffort);
    }

    private void RefreshProviderOptions()
    {
        var selectedSupervisor = SettingsSurface.SupervisorProvider.SelectedValue as string;
        var selectedWorker = SettingsSurface.WorkerProvider.SelectedValue as string;
        var options = new Dictionary<string, ProviderOption>(StringComparer.OrdinalIgnoreCase);
        foreach (var template in ViewModel.ProviderTemplates)
        {
            if (template.Id is "openai" or "ollama" or "lmstudio")
            {
                options[template.Id] = new ProviderOption { Id = template.Id, Name = template.Name };
            }
        }
        foreach (var provider in ViewModel.Providers)
        {
            options[provider.Id] = new ProviderOption { Id = provider.Id, Name = provider.DisplayName };
        }
        MainPageViewModel.Replace(ViewModel.ProviderOptions, options.Values.OrderBy(item => item.Name));
        if (selectedSupervisor is not null)
        {
            SettingsSurface.SupervisorProvider.SelectedValue = selectedSupervisor;
        }
        if (selectedWorker is not null)
        {
            SettingsSurface.WorkerProvider.SelectedValue = selectedWorker;
        }
    }

    private async void OnChooseRepository(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new Windows.Storage.Pickers.FolderPicker();
            picker.FileTypeFilter.Add("*");
            WinRT.Interop.InitializeWithWindow.Initialize(picker, App.WindowHandle);
            var folder = await picker.PickSingleFolderAsync();
            if (folder is null)
            {
                return;
            }

            AgentRuntimeProcess.RememberRepository(folder.Path);
            _runtime.Stop();
            _api?.Dispose();
            _api = null;
            ResetWorkspace();
            await StartRuntimeAsync(folder.Path);
        }
        catch (Exception exception)
        {
            ShowStartupError(exception.Message);
        }
    }

    private void OnNewTask(object sender, RoutedEventArgs e)
    {
        ThreadList.SelectedItem = null;
        ResetWorkspace();
        ShowWorkspace();
        WorkspaceSurface.ComposerView.FocusPrompt();
    }

    private void ResetWorkspace()
    {
        ClearAttachments();
        _currentPlan = null;
        _currentThreadId = null;
        _currentTurnId = null;
        _currentPlanJobId = null;
        _currentFarmJobId = null;
        _lastFarmJobId = null;
        ViewModel.Execution.Reset();
        ViewModel.Timeline.Clear();
        ViewModel.PlannedWorkers.Clear();
        ViewModel.LiveAgents.Clear();
        WorkspaceSurface.ExecutionView.RefreshEmptyState();
        ViewModel.CurrentTitle = "New task";
        ViewModel.CurrentSubtitle = "Describe an outcome. The Supervisor will plan before Workers execute.";
        WorkspaceSurface.ComposerView.PromptText = string.Empty;
    }

    private void ShowWorkspace() => ViewModel.Shell.ShowWorkspaceCommand.Execute(null);

    private void ShowRuns() => ViewModel.Shell.ShowRunsCommand.Execute(null);

    private void ShowSettings() => ViewModel.Shell.ShowSettingsCommand.Execute(null);

    public static Visibility BoolToVisibility(bool value) =>
        value ? Visibility.Visible : Visibility.Collapsed;

    private async void OnRefresh(object sender, RoutedEventArgs e)
    {
        try
        {
            ShowInfo("Refreshing", "Reading native task and run state…", InfoBarSeverity.Informational);
            var bootstrap = await RequireApi().GetBootstrapAsync(_lifetime.Token);
            ApplyBootstrap(bootstrap);
            if (_currentThreadId is not null)
            {
                await OpenThreadAsync(_currentThreadId);
            }
            ShowInfo("Up to date", "The workspace reflects the latest local runtime state.", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowInfo("Refresh failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnThreadSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ThreadList.SelectedItem is not ThreadSummary thread)
        {
            return;
        }

        try
        {
            await OpenThreadAsync(thread.ThreadId);
        }
        catch (Exception exception)
        {
            ShowInfo("Thread could not be opened", exception.Message, InfoBarSeverity.Error);
        }
    }

    private void OnThreadSearchChanged(AutoSuggestBox sender, AutoSuggestBoxTextChangedEventArgs args)
    {
        if (args.Reason != AutoSuggestionBoxTextChangeReason.UserInput)
        {
            return;
        }
        ViewModel.ThreadQuery = sender.Text;
        ViewModel.ApplyThreadFilter();
    }

    private static string? ThreadIdFrom(object sender) =>
        sender is FrameworkElement { Tag: string threadId } ? threadId : null;

    private async void OnResumeThread(object sender, RoutedEventArgs e)
    {
        var threadId = ThreadIdFrom(sender);
        if (threadId is null) return;
        try
        {
            await RequireApi().ArchiveThreadAsync(threadId, false, _lifetime.Token);
            await RefreshBootstrapAsync();
            await OpenThreadAsync(threadId);
            WorkspaceSurface.ComposerView.FocusPrompt();
        }
        catch (Exception exception)
        {
            ShowInfo("Thread could not be resumed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnRenameThread(object sender, RoutedEventArgs e)
    {
        var threadId = ThreadIdFrom(sender);
        var thread = ViewModel.ThreadCatalog.FirstOrDefault(item => item.ThreadId == threadId);
        if (thread is null) return;
        var input = new TextBox { Text = thread.Title, SelectionStart = thread.Title.Length };
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "Rename thread",
            Content = input,
            PrimaryButtonText = "Rename",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(input.Text)) return;
        try
        {
            await RequireApi().RenameThreadAsync(thread.ThreadId, input.Text, _lifetime.Token);
            await RefreshBootstrapAsync();
        }
        catch (Exception exception)
        {
            ShowInfo("Thread could not be renamed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnForkThread(object sender, RoutedEventArgs e)
    {
        var threadId = ThreadIdFrom(sender);
        if (threadId is null) return;
        try
        {
            var forked = await RequireApi().ForkThreadAsync(threadId, null, _lifetime.Token);
            await RefreshBootstrapAsync();
            await OpenThreadAsync(forked.ThreadId);
            ShowInfo("Thread forked", "A new independent thread was created from the selected history.", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowInfo("Thread could not be forked", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnArchiveThread(object sender, RoutedEventArgs e)
    {
        var threadId = ThreadIdFrom(sender);
        if (threadId is null) return;
        try
        {
            await RequireApi().ArchiveThreadAsync(threadId, true, _lifetime.Token);
            if (_currentThreadId == threadId) ResetWorkspace();
            await RefreshBootstrapAsync();
        }
        catch (Exception exception)
        {
            ShowInfo("Thread could not be archived", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnDeleteThread(object sender, RoutedEventArgs e)
    {
        var threadId = ThreadIdFrom(sender);
        var thread = ViewModel.ThreadCatalog.FirstOrDefault(item => item.ThreadId == threadId);
        if (thread is null || !await ConfirmAsync("Delete thread?", $"Delete “{thread.Title}” and its local history? This cannot be undone.")) return;
        try
        {
            await RequireApi().DeleteThreadAsync(thread.ThreadId, _lifetime.Token);
            if (_currentThreadId == thread.ThreadId) ResetWorkspace();
            await RefreshBootstrapAsync();
        }
        catch (Exception exception)
        {
            ShowInfo("Thread could not be deleted", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async Task OpenThreadAsync(string threadId, bool preserveLiveOutput = false)
    {
        ClearAttachments();
        var thread = await RequireApi().GetThreadAsync(threadId, _lifetime.Token);
        _currentThreadId = thread.ThreadId;
        _currentTurnId = thread.Turns.LastOrDefault()?.TurnId;
        ViewModel.CurrentTitle = thread.Title;
        ViewModel.CurrentSubtitle = $"{thread.Turns.Count} turn{(thread.Turns.Count == 1 ? string.Empty : "s")} · {thread.Status.Replace('_', ' ')}";
        ViewModel.Timeline.Clear();
        ViewModel.PlannedWorkers.Clear();
        if (!preserveLiveOutput)
        {
            ViewModel.LiveAgents.Clear();
            WorkspaceSurface.ExecutionView.RefreshEmptyState();
        }
        _currentPlan = null;

        foreach (var turn in thread.Turns)
        {
            foreach (var item in turn.Items)
            {
                AppendThreadItem(item);
            }
        }

        ViewModel.Execution.CanStart = _currentPlan is not null && thread.Status != "running";
        ViewModel.Execution.PlanState = _currentPlan is null
            ? thread.Status.Replace('_', ' ')
            : $"{_currentPlan.Workers.Count} workers ready";
        ShowWorkspace();
    }

    private void AppendThreadItem(ThreadItem item)
    {
        switch (item.Type)
        {
            case "user_message":
            {
                var text = ReadPayloadString(item.Payload, "text") ?? string.Empty;
                WorkspaceSurface.ComposerView.PromptText = text;
                var attachmentNames = ReadAttachmentNames(item.Payload);
                var body = attachmentNames.Count == 0
                    ? text
                    : $"{text}{Environment.NewLine}{Environment.NewLine}Attached: {string.Join(", ", attachmentNames)}";
                ViewModel.Timeline.Add(new TimelineEntry
                {
                    Actor = TimelineActivityActor.User,
                    Title = "Task request",
                    Body = body,
                    State = TimelineActivityState.Completed,
                });
                break;
            }
            case "supervisor_plan":
            {
                if (item.Payload.TryGetProperty("plan", out var rawPlan))
                {
                    var plan = rawPlan.Deserialize<WorkerPlan>(JsonOptions);
                    if (plan is not null)
                    {
                        ApplyPlan(plan, addTimeline: true);
                    }
                }
                else
                {
                    ViewModel.Timeline.Add(new TimelineEntry
                    {
                        Actor = TimelineActivityActor.Supervisor,
                        Title = "Planning",
                        Body = "The Supervisor is reading the repository and assigning isolated Worker scopes.",
                        State = TimelineEntry.ParseState(item.Status),
                    });
                }
                break;
            }
            case "farm_run":
            {
                var farmId = ReadPayloadString(item.Payload, "farm_id");
                var error = ReadNestedPayloadString(item.Payload, "error", "message");
                ViewModel.Timeline.Add(new TimelineEntry
                {
                    Actor = TimelineActivityActor.WorkerFarm,
                    Title = farmId is null ? "Worker execution" : "Worker execution finished",
                    Body = error ?? (farmId is null
                        ? "Workers are executing the approved plan in isolated worktrees."
                        : $"Run {farmId} is ready for review in Run history."),
                    State = TimelineEntry.ParseState(item.Status),
                });
                break;
            }
            case "supervisor_decision":
                ViewModel.Timeline.Add(new TimelineEntry
                {
                    Actor = TimelineActivityActor.Review,
                    Title = "Final review",
                    Body = "The Supervisor recorded its final decision and evidence review.",
                    State = TimelineEntry.ParseState(item.Status),
                });
                break;
        }
    }

    private async void OnPlanTask(object sender, RoutedEventArgs e)
    {
        var request = WorkspaceSurface.ComposerView.PromptText.Trim();
        if (string.IsNullOrWhiteSpace(request) && ViewModel.Attachments.Count == 0)
        {
            ShowInfo("Task required", "Describe an outcome or attach files for the agents to analyze.", InfoBarSeverity.Warning);
            return;
        }

        if (string.IsNullOrWhiteSpace(request))
        {
            request = "Analyze the attached files and summarize the important findings.";
        }

        try
        {
            SetPlanningState(true, "Supervisor is planning…");
            var api = RequireApi();
            if (_currentThreadId is null)
            {
                var thread = await api.CreateThreadAsync(
                    new CreateThreadRequest { Title = request },
                    _lifetime.Token);
                _currentThreadId = thread.ThreadId;
            }

            ViewModel.Timeline.Add(new TimelineEntry
            {
                Actor = TimelineActivityActor.User,
                Title = "Task request",
                Body = DescribeRequest(request),
                State = TimelineActivityState.Completed,
            });
            ViewModel.Timeline.Add(new TimelineEntry
            {
                Actor = TimelineActivityActor.Supervisor,
                Title = "Planning the farm",
                Body = "Reading repository boundaries, choosing economical Worker routes, and drafting acceptance criteria.",
                State = TimelineActivityState.Running,
            });
            PreparePlanningLiveOutput();

            var workerCount = WorkspaceSurface.ComposerView.WorkerCount;
            var initial = await api.CreatePlanAsync(new PlanRequest
            {
                Request = request,
                BaseRef = WorkspaceSurface.ComposerView.BaseReference,
                WorkerCount = workerCount,
                ThreadId = _currentThreadId,
                Attachments = ViewModel.Attachments.Select(item => item.Id).ToList(),
            }, _lifetime.Token);
            _currentPlanJobId = initial.JobId;
            ViewModel.Execution.BeginPlanning();
            _currentTurnId = initial.TurnId;

            var completed = await PollPlanAsync(initial.JobId);
            _currentThreadId = completed.ThreadId ?? _currentThreadId;
            _currentTurnId = completed.TurnId ?? _currentTurnId;
            if (completed.Plan is null)
            {
                throw new InvalidOperationException("The Supervisor completed without returning a Worker Plan.");
            }

            ApplyPlan(completed.Plan, addTimeline: false);
            ViewModel.Timeline.Add(new TimelineEntry
            {
                Actor = TimelineActivityActor.Supervisor,
                Title = "Worker Plan ready",
                Body = DescribePlan(completed.Plan),
                State = TimelineActivityState.Review,
            });
            ShowInfo("Plan ready", "Review the Worker assignments, then start execution.", InfoBarSeverity.Success);
            await RefreshBootstrapAsync();
        }
        catch (Exception exception)
        {
            ViewModel.Timeline.Add(new TimelineEntry
            {
                Actor = TimelineActivityActor.Supervisor,
                Title = "Planning failed",
                Body = exception.Message,
                State = TimelineActivityState.Failed,
            });
            ShowInfo("Planning failed", exception.Message, InfoBarSeverity.Error);
            ViewModel.Execution.MarkFailed("Planning failed", canRetry: false);
        }
        finally
        {
            _currentPlanJobId = null;
            if (_currentPlan is null)
            {
                ViewModel.Execution.CanCancel = _currentFarmJobId is not null;
            }
            SetPlanningState(false, "Plan task");
        }
    }

    private void OnComposerPlanRequested(object? sender, EventArgs args) =>
        OnPlanTask(WorkspaceSurface.ComposerView, new RoutedEventArgs());

    private void OnComposerAttachFilesRequested(object? sender, EventArgs args) =>
        OnAttachFiles(WorkspaceSurface.ComposerView, new RoutedEventArgs());

    private void OnComposerConfigureRoutingRequested(object? sender, EventArgs args) => ShowSettings();

    private async void OnComposerFilesDropped(object? sender, ComposerFilesEventArgs args) =>
        await AddFilesAsync(args.Files);

    private void OnComposerDropFailed(object? sender, ComposerErrorEventArgs args) =>
        ShowInfo("Files could not be attached", args.Exception.Message, InfoBarSeverity.Error);

    private async void OnAttachFiles(object sender, RoutedEventArgs e)
    {
        if (_planning)
        {
            return;
        }

        try
        {
            var picker = new FileOpenPicker
            {
                SuggestedStartLocation = PickerLocationId.DocumentsLibrary,
                ViewMode = PickerViewMode.List,
            };
            picker.FileTypeFilter.Add("*");
            WinRT.Interop.InitializeWithWindow.Initialize(picker, App.WindowHandle);
            var files = await picker.PickMultipleFilesAsync();
            if (files.Count > 0)
            {
                await AddFilesAsync(files);
            }
        }
        catch (Exception exception)
        {
            ShowInfo("Files could not be attached", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async Task AddFilesAsync(IEnumerable<StorageFile> files)
    {
        const int maximumAttachments = 8;
        var availableSlots = maximumAttachments - ViewModel.Attachments.Count;
        if (availableSlots <= 0)
        {
            ShowInfo("Attachment limit reached", "A task can include up to 8 files.", InfoBarSeverity.Warning);
            return;
        }

        var selectedFiles = files.Take(availableSlots).ToList();
        var added = 0;
        var failures = new List<string>();
        foreach (var file in selectedFiles)
        {
            StorageFile? temporaryCopy = null;
            try
            {
                var localPath = file.Path;
                if (string.IsNullOrWhiteSpace(localPath))
                {
                    temporaryCopy = await file.CopyAsync(
                        ApplicationData.Current.TemporaryFolder,
                        file.Name,
                        NameCollisionOption.GenerateUniqueName);
                    localPath = temporaryCopy.Path;
                }

                var attachment = await RequireApi().AddAttachmentAsync(
                    new AddAttachmentRequest { LocalPath = localPath },
                    _lifetime.Token);
                ViewModel.Attachments.Add(attachment);
                added++;
            }
            catch (Exception exception)
            {
                failures.Add($"{file.Name}: {exception.Message}");
            }
            finally
            {
                if (temporaryCopy is not null)
                {
                    try
                    {
                        await temporaryCopy.DeleteAsync(StorageDeleteOption.PermanentDelete);
                    }
                    catch
                    {
                        // The runtime already owns its staged copy; temp cleanup is best effort.
                    }
                }
            }
        }

        if (added > 0)
        {
            InvalidatePlanForAttachmentChange();
        }
        if (failures.Count > 0)
        {
            ShowInfo(
                added > 0 ? "Some files were not attached" : "Files could not be attached",
                string.Join(Environment.NewLine, failures),
                added > 0 ? InfoBarSeverity.Warning : InfoBarSeverity.Error);
        }
        else if (added > 0)
        {
            ShowInfo(
                added == 1 ? "File attached" : "Files attached",
                $"{added} file{(added == 1 ? string.Empty : "s")} will be available to the Supervisor and Workers.",
                InfoBarSeverity.Success);
        }
    }

    private async void OnComposerAttachmentRemovalRequested(object? sender, ComposerAttachmentEventArgs args)
    {
        if (_planning)
        {
            return;
        }

        var attachmentId = args.AttachmentId;
        var attachment = ViewModel.Attachments.FirstOrDefault(item => item.Id == attachmentId);
        if (attachment is null)
        {
            return;
        }

        try
        {
            await RequireApi().RemoveAttachmentAsync(attachmentId, _lifetime.Token);
            ViewModel.Attachments.Remove(attachment);
            InvalidatePlanForAttachmentChange();
        }
        catch (Exception exception)
        {
            ShowInfo("Attachment could not be removed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private void ClearAttachments()
    {
        var api = _api;
        var ids = ViewModel.Attachments.Select(item => item.Id).ToArray();
        ViewModel.Attachments.Clear();
        if (api is not null && ids.Length > 0)
        {
            _ = RemoveAttachmentsBestEffortAsync(api, ids);
        }
    }

    private static async Task RemoveAttachmentsBestEffortAsync(AgentFarmApiClient api, IEnumerable<string> ids)
    {
        foreach (var id in ids)
        {
            try
            {
                await api.RemoveAttachmentAsync(id, CancellationToken.None);
            }
            catch
            {
                // The backend session removes any remaining staged files when it exits.
            }
        }
    }

    private void InvalidatePlanForAttachmentChange()
    {
        if (_currentPlan is null)
        {
            return;
        }

        _currentPlan = null;
        ViewModel.PlannedWorkers.Clear();
        ViewModel.Execution.CanStart = false;
        ViewModel.Execution.PlanState = "Attachments changed — plan again";
    }

    private string DescribeRequest(string request)
    {
        if (ViewModel.Attachments.Count == 0)
        {
            return request;
        }

        return $"{request}{Environment.NewLine}{Environment.NewLine}Attached: "
            + string.Join(", ", ViewModel.Attachments.Select(item => item.Name));
    }

    private async Task<PlanJobResponse> PollPlanAsync(string jobId)
    {
        long after = 0;
        while (true)
        {
            _lifetime.Token.ThrowIfCancellationRequested();
            var api = RequireApi();
            try
            {
                await foreach (var envelope in api.StreamPlanJobEventsAsync(
                    jobId,
                    after,
                    _lifetime.Token))
                {
                    if (envelope.Sequence <= after)
                    {
                        continue;
                    }
                    after = envelope.Sequence;
                    ApplyLiveEvents([envelope.Event]);
                    ViewModel.Execution.SetQueued("Supervisor planning");
                }

                var job = await api.GetPlanJobAsync(jobId, _lifetime.Token);
                switch (job.Status)
                {
                    case "COMPLETED":
                        return job;
                    case "FAILED":
                    case "INTERRUPTED":
                    case "CANCELLED":
                        throw new InvalidOperationException(
                            job.Error?.Message ?? "Supervisor planning was interrupted.");
                    case "QUEUED":
                        ViewModel.Execution.SetQueued("Supervisor queued");
                        break;
                    default:
                        ViewModel.Execution.SetQueued("Supervisor planning");
                        break;
                }
            }
            catch (Exception exception) when (IsRecoverableStreamFailure(exception))
            {
                await Task.Delay(TimeSpan.FromMilliseconds(500), _lifetime.Token);
            }
        }
    }

    private void ApplyPlan(WorkerPlan plan, bool addTimeline)
    {
        _currentPlan = plan;
        MainPageViewModel.Replace(ViewModel.PlannedWorkers, plan.Workers);
        ViewModel.CurrentTitle = string.IsNullOrWhiteSpace(plan.TaskId) ? "Execution plan" : plan.TaskId;
        ViewModel.CurrentSubtitle = $"{plan.Workers.Count} isolated Worker assignment{(plan.Workers.Count == 1 ? string.Empty : "s")} · base {plan.BaseRef}";
        ViewModel.Execution.SetPlanReady(plan.Workers.Count);
        WorkspaceSurface.ExecutionView.ShowPlan();
        if (addTimeline)
        {
            ViewModel.Timeline.Add(new TimelineEntry
            {
                Actor = TimelineActivityActor.Supervisor,
                Title = "Worker Plan",
                Body = DescribePlan(plan),
                State = TimelineActivityState.Ready,
            });
        }
    }

    private static string DescribePlan(WorkerPlan plan)
    {
        var routes = string.Join(
            Environment.NewLine,
            plan.Workers.Select(worker => $"• {worker.Role} — {worker.Profile}: {worker.Goal}"));
        var deliverable = plan.Deliverable is null
            ? string.Empty
            : $"{Environment.NewLine}Deliverable: {plan.Deliverable.Path}";
        return routes + deliverable;
    }

    private async void OnStartFarm(object sender, RoutedEventArgs e)
    {
        if (_currentPlan is null)
        {
            return;
        }

        try
        {
            ViewModel.Execution.BeginFarmStart();
            PrepareWorkerLiveOutputs(_currentPlan);
            var initial = await RequireApi().StartFarmAsync(
                FarmSubmission.FromPlan(_currentPlan,
                    _currentThreadId,
                    _currentTurnId,
                    ViewModel.Attachments.Select(item => item.Id)),
                _lifetime.Token);
            _currentFarmJobId = initial.JobId;
            _lastFarmJobId = initial.JobId;
            ViewModel.Execution.MarkRunning();
            ViewModel.Execution.CancelAutomationName = "Cancel Farm execution";
            _currentTurnId = initial.TurnId ?? _currentTurnId;
            ViewModel.Timeline.Add(new TimelineEntry
            {
                Actor = TimelineActivityActor.WorkerFarm,
                Title = "Workers started",
                Body = "The plan is running in isolated worktrees. Model calls and file changes are owned by the local backend.",
                State = TimelineActivityState.Running,
            });

            var completed = await PollFarmAsync(initial.JobId);
            if (completed.FarmId is not null)
            {
                ViewModel.Timeline.Add(new TimelineEntry
                {
                    Actor = TimelineActivityActor.WorkerFarm,
                    Title = "Execution completed",
                    Body = $"Run {completed.FarmId} is ready. Open Run history for Worker evidence and the synthesized deliverable.",
                    State = TimelineActivityState.Completed,
                });
            }
            ShowInfo("Farm completed", "Worker evidence and results are available in Run history.", InfoBarSeverity.Success);
            await RefreshBootstrapAsync();
            if (_currentThreadId is not null)
            {
                await OpenThreadAsync(_currentThreadId, preserveLiveOutput: true);
            }
        }
        catch (Exception exception)
        {
            ViewModel.Timeline.Add(new TimelineEntry
            {
                Actor = TimelineActivityActor.WorkerFarm,
                Title = "Execution failed",
                Body = exception.Message,
                State = TimelineActivityState.Failed,
            });
            ShowInfo("Execution failed", exception.Message, InfoBarSeverity.Error);
            ViewModel.Execution.MarkFailed("Execution failed", canRetry: _currentPlan is not null);
        }
        finally
        {
            _currentFarmJobId = null;
            if (ViewModel.Execution.Lifecycle != ExecutionLifecycle.Failed)
            {
                ViewModel.Execution.Recover(
                    _currentPlan is not null,
                    _currentPlanJobId is not null,
                    ViewModel.Execution.PlanState);
            }
        }
    }

    private async Task<FarmJobResponse> PollFarmAsync(string jobId)
    {
        long after = 0;
        while (true)
        {
            _lifetime.Token.ThrowIfCancellationRequested();
            var api = RequireApi();
            try
            {
                await foreach (var envelope in api.StreamFarmJobEventsAsync(
                    jobId,
                    after,
                    _lifetime.Token))
                {
                    if (envelope.Sequence <= after)
                    {
                        continue;
                    }
                    after = envelope.Sequence;
                    ApplyLiveEvents([envelope.Event]);
                    ViewModel.Execution.MarkRunning();
                }

                var job = await api.GetFarmJobAsync(jobId, _lifetime.Token);
                if (job.Status == "COMPLETED")
                {
                    ViewModel.Execution.MarkCompleted();
                    return job;
                }
                if (job.Status is "FAILED" or "INTERRUPTED" or "CANCELLED")
                {
                    throw new InvalidOperationException(
                        job.Error?.Message ?? "Farm execution was interrupted.");
                }
                ViewModel.Execution.MarkRunning(job.Status == "QUEUED" ? "Farm queued" : "Workers running");
            }
            catch (Exception exception) when (IsRecoverableStreamFailure(exception))
            {
                await Task.Delay(TimeSpan.FromMilliseconds(500), _lifetime.Token);
            }
        }
    }

    private static bool IsRecoverableStreamFailure(Exception exception) =>
        exception is HttpRequestException or IOException or ObjectDisposedException ||
        exception is AgentFarmApiException
        {
            StatusCode: System.Net.HttpStatusCode.NotFound
                or System.Net.HttpStatusCode.RequestTimeout
                or System.Net.HttpStatusCode.BadGateway
                or System.Net.HttpStatusCode.ServiceUnavailable
                or System.Net.HttpStatusCode.GatewayTimeout
        };

    private void PreparePlanningLiveOutput()
    {
        ViewModel.LiveAgents.Clear();
        ViewModel.LiveAgents.Add(new LiveAgentOutput
        {
            Id = "supervisor",
            Kind = "Supervisor",
            DisplayName = "Planning Supervisor",
            Route = ViewModel.SupervisorRoute,
            Status = "Starting",
            Activity = "Connecting to the model",
            IsActive = true,
        });
        WorkspaceSurface.ExecutionView.RefreshEmptyState();
        WorkspaceSurface.ExecutionView.ShowLive();
    }

    private void PrepareWorkerLiveOutputs(WorkerPlan plan)
    {
        foreach (var worker in plan.Workers)
        {
            if (ViewModel.LiveAgents.Any(item => item.Id == worker.Id))
            {
                continue;
            }
            var profile = ViewModel.ActiveProfiles.FirstOrDefault(item => item.Name == worker.Profile);
            ViewModel.LiveAgents.Add(new LiveAgentOutput
            {
                Id = worker.Id,
                Kind = "Worker",
                DisplayName = worker.Role,
                Route = profile?.RouteDescription ?? worker.Profile,
                Status = "Queued",
                Activity = "Waiting for an execution slot",
                DependencyLabel = worker.DependencyLabel,
                Progress = 0,
            });
        }
        WorkspaceSurface.ExecutionView.RefreshEmptyState();
        WorkspaceSurface.ExecutionView.ShowLive();
    }

    private void ApplyLiveEvents(IEnumerable<JobEvent> events)
    {
        LiveAgentOutput? latest = null;
        foreach (var jobEvent in events)
        {
            if (jobEvent.Type is "approval.requested" or "approval.resolved")
            {
                var approval = ReadApproval(jobEvent.Approval);
                if (approval is not null)
                {
                    ApplyApprovalEvent(jobEvent.Type, approval);
                    if (string.IsNullOrWhiteSpace(jobEvent.AgentId))
                    {
                        jobEvent.AgentId = approval.AgentId;
                        jobEvent.DisplayName = approval.AgentName;
                        jobEvent.AgentKind = approval.AgentKind;
                        jobEvent.Profile = approval.Profile;
                        jobEvent.Provider = approval.Provider;
                        jobEvent.Model = approval.Model;
                    }
                }
            }
            if (string.IsNullOrWhiteSpace(jobEvent.AgentId))
            {
                continue;
            }
            var agent = ViewModel.LiveAgents.FirstOrDefault(item => item.Id == jobEvent.AgentId);
            if (agent is null)
            {
                agent = new LiveAgentOutput
                {
                    Id = jobEvent.AgentId,
                    Kind = jobEvent.AgentKind == "supervisor" ? "Supervisor" : "Worker",
                    DisplayName = string.IsNullOrWhiteSpace(jobEvent.DisplayName)
                        ? jobEvent.AgentId
                        : jobEvent.DisplayName,
                    Route = string.IsNullOrWhiteSpace(jobEvent.Profile) ? "Resolving route" : jobEvent.Profile,
                };
                ViewModel.LiveAgents.Add(agent);
            }
            WorkspaceSurface.ExecutionView.RefreshEmptyState();
            if (!string.IsNullOrWhiteSpace(jobEvent.Provider) || !string.IsNullOrWhiteSpace(jobEvent.Model))
            {
                agent.Route = string.Join(
                    " · ",
                    new[] { jobEvent.Provider, jobEvent.Model }.Where(value => !string.IsNullOrWhiteSpace(value)));
            }
            if (jobEvent.DependsOn.Count > 0)
            {
                agent.DependencyLabel = "After " + string.Join(", ", jobEvent.DependsOn);
            }
            if (jobEvent.Progress is double progress)
            {
                agent.Progress = Math.Clamp(progress, 0, 100);
            }

            ApplyLiveEvent(agent, jobEvent);
            latest = agent;
        }
        if (latest is not null)
        {
            WorkspaceSurface.ExecutionView.ShowLive();
            WorkspaceSurface.ExecutionView.ScrollTo(latest);
        }
    }

    private static void ApplyLiveEvent(LiveAgentOutput agent, JobEvent jobEvent)
    {
        switch (jobEvent.Type)
        {
            case "worker.queued":
                agent.Status = "Queued";
                agent.Activity = jobEvent.DependsOn.Count == 0
                    ? "Waiting for an execution slot"
                    : "Waiting for dependencies";
                agent.Progress = 0;
                agent.IsActive = false;
                agent.CanRetry = false;
                break;
            case "worker.ready":
                agent.Status = "Ready";
                agent.Activity = "Dependencies satisfied";
                agent.Progress = Math.Max(agent.Progress, 5);
                agent.IsActive = true;
                agent.CanRetry = false;
                break;
            case "worker.started":
                agent.Status = "Starting";
                agent.Activity = "Preparing an isolated worktree";
                agent.IsActive = true;
                agent.Progress = Math.Max(agent.Progress, 10);
                agent.CanRetry = false;
                break;
            case "agent.started":
                agent.Status = "Running";
                agent.Activity = "Agent connected";
                agent.IsActive = true;
                break;
            case "turn.started":
                agent.Status = "Running";
                agent.Activity = jobEvent.Turn is null ? "Starting model turn" : $"Turn {jobEvent.Turn}";
                agent.IsActive = true;
                break;
            case "model.request.started":
                agent.Activity = "Waiting for model response";
                agent.IsActive = true;
                break;
            case "model.output.delta":
                agent.Status = "Streaming";
                agent.Activity = "Receiving model output";
                agent.IsActive = true;
                agent.AppendOutput(jobEvent.Delta);
                agent.Progress = Math.Max(agent.Progress, 45);
                break;
            case "model.reasoning.delta":
                agent.Status = "Reasoning";
                agent.Activity = "Reasoning…";
                agent.IsActive = true;
                break;
            case "model.request.retrying":
                agent.Status = "Retrying";
                agent.Activity = "Reconnecting to the model";
                agent.IsActive = true;
                agent.CanRetry = false;
                break;
            case "model.request.completed":
                agent.Status = "Running";
                agent.Activity = "Model response received";
                break;
            case "item.started":
                agent.Activity = ToolActivity(jobEvent.Item, "Using");
                agent.IsActive = true;
                break;
            case "approval.requested":
                agent.Status = "Approval required";
                agent.Activity = "Waiting for your decision";
                agent.IsActive = false;
                break;
            case "approval.resolved":
                agent.Status = "Running";
                agent.Activity = "Approval decision received";
                agent.IsActive = true;
                break;
            case "item.completed":
                if (ReadEventItem(jobEvent.Item, "type") == "agent_message")
                {
                    agent.AppendMessageIfMissing(ReadEventItem(jobEvent.Item, "text"));
                    agent.Activity = "Model message completed";
                }
                else
                {
                    agent.Activity = ToolActivity(jobEvent.Item, "Completed");
                }
                break;
            case "turn.completed":
                agent.Activity = jobEvent.Turn is null ? "Turn completed" : $"Turn {jobEvent.Turn} completed";
                break;
            case "worker.completed":
            case "agent.completed":
                agent.Status = "Completed";
                agent.Activity = "Finished";
                agent.IsActive = false;
                agent.Progress = 100;
                agent.CanRetry = false;
                break;
            case "worker.cancel_requested":
                agent.Status = "Cancelling";
                agent.Activity = "Cancellation requested";
                agent.IsActive = true;
                break;
            case "worker.cancelled":
            case "agent.cancelled":
                agent.Status = "Cancelled";
                agent.Activity = "Stopped by user";
                agent.IsActive = false;
                agent.Progress = 100;
                agent.CanRetry = true;
                break;
            case "worker.blocked":
                agent.Status = "Blocked";
                agent.Activity = string.IsNullOrWhiteSpace(jobEvent.Error) ? "A dependency failed" : jobEvent.Error;
                agent.IsActive = false;
                agent.Progress = 100;
                agent.CanRetry = true;
                break;
            case "worker.failed":
            case "agent.failed":
                agent.Status = "Failed";
                agent.Activity = string.IsNullOrWhiteSpace(jobEvent.Error) ? "Execution failed" : jobEvent.Error;
                agent.AppendMessageIfMissing(jobEvent.Error);
                agent.IsActive = false;
                agent.Progress = 100;
                agent.CanRetry = true;
                break;
            case "worker.escalating":
                agent.Status = "Retrying";
                agent.Activity = "Escalating to a stronger route";
                agent.IsActive = true;
                agent.Progress = Math.Max(agent.Progress, 60);
                agent.CanRetry = false;
                break;
        }
    }

    private static ApprovalRequest? ReadApproval(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Object)
        {
            return null;
        }
        try
        {
            return value.Deserialize<ApprovalRequest>(JsonOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private void ApplyApprovalEvent(string eventType, ApprovalRequest approval)
    {
        var existing = ViewModel.PendingApprovals.FirstOrDefault(item => item.Id == approval.Id);
        if (eventType == "approval.requested" && approval.Status == "pending")
        {
            if (existing is null)
            {
                ViewModel.PendingApprovals.Add(approval);
            }
            WorkspaceSurface.ExecutionView.ShowLive();
            WorkspaceSurface.ExecutionView.RefreshEmptyState();
            return;
        }
        if (existing is not null)
        {
            ViewModel.PendingApprovals.Remove(existing);
        }
        WorkspaceSurface.ExecutionView.RefreshEmptyState();
    }

    private async void OnExecutionApprovalRequested(object? sender, ExecutionApprovalEventArgs args) =>
        await ResolveApprovalAsync(args.ApprovalId, args.Decision);

    private async Task ResolveApprovalAsync(string approvalId, string decision)
    {
        try
        {
            var resolved = await RequireApi().ResolveApprovalAsync(
                approvalId,
                decision,
                _lifetime.Token);
            var existing = ViewModel.PendingApprovals.FirstOrDefault(item => item.Id == resolved.Id);
            if (existing is not null)
            {
                ViewModel.PendingApprovals.Remove(existing);
            }
        }
        catch (Exception exception)
        {
            ShowInfo("Approval failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private void OnExecutionStartRequested(object? sender, EventArgs args) =>
        OnStartFarm(WorkspaceSurface.ExecutionView, new RoutedEventArgs());

    private void OnExecutionCancelRequested(object? sender, EventArgs args) =>
        OnCancelRun();

    private async void OnCancelRun()
    {
        ViewModel.Execution.BeginCancelling(
            _currentPlanJobId is not null ? "Cancelling Supervisor" : "Cancelling Farm");
        try
        {
            if (_currentPlanJobId is not null)
            {
                await RequireApi().CancelPlanAsync(_currentPlanJobId, _lifetime.Token);
                return;
            }
            if (_currentFarmJobId is not null)
            {
                await RequireApi().CancelFarmAsync(_currentFarmJobId, _lifetime.Token);
            }
        }
        catch (Exception exception)
        {
            ViewModel.Execution.CanCancel = _currentPlanJobId is not null || _currentFarmJobId is not null;
            ShowInfo("Cancellation failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnExecutionWorkerCancelRequested(object? sender, ExecutionWorkerEventArgs args)
    {
        if (_currentFarmJobId is null)
        {
            return;
        }
        var workerId = args.WorkerId;
        try
        {
            await RequireApi().CancelWorkerAsync(_currentFarmJobId, workerId, _lifetime.Token);
            var agent = ViewModel.LiveAgents.FirstOrDefault(item => item.Id == workerId);
            if (agent is not null)
            {
                agent.Status = "Cancelling";
                agent.Activity = "Cancellation requested";
            }
        }
        catch (Exception exception)
        {
            ShowInfo("Worker cancellation failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnExecutionWorkerRetryRequested(object? sender, ExecutionWorkerEventArgs args)
    {
        var sourceJobId = _currentFarmJobId ?? _lastFarmJobId;
        if (sourceJobId is null)
        {
            ShowInfo("Retry unavailable", "No recoverable Farm job is associated with this workspace.", InfoBarSeverity.Warning);
            return;
        }
        var agent = ViewModel.LiveAgents.FirstOrDefault(item => item.Id == args.WorkerId);
        try
        {
            if (agent is not null)
            {
                agent.CanRetry = false;
                agent.Status = "Retrying";
                agent.Activity = "Starting a recovery job";
                agent.Progress = 0;
            }
            var retried = await RequireApi().RetryWorkerAsync(sourceJobId, args.WorkerId, _lifetime.Token);
            _currentFarmJobId = retried.JobId;
            _lastFarmJobId = retried.JobId;
            ViewModel.Execution.MarkRunning($"Retrying {args.WorkerId}");
            ShowInfo("Worker retry started", $"{args.WorkerId} is running in a new recovery job.", InfoBarSeverity.Informational);
            await PollFarmAsync(retried.JobId);
            ShowInfo("Worker retry completed", $"{args.WorkerId} completed its recovery job.", InfoBarSeverity.Success);
            await RefreshBootstrapAsync();
        }
        catch (Exception exception)
        {
            if (agent is not null)
            {
                agent.CanRetry = true;
                agent.Status = "Failed";
                agent.Activity = exception.Message;
            }
            ViewModel.Execution.MarkFailed("Worker retry failed", canRetry: _currentPlan is not null);
            ShowInfo("Worker retry failed", exception.Message, InfoBarSeverity.Error);
        }
        finally
        {
            _currentFarmJobId = null;
        }
    }

    private static string ToolActivity(JsonElement item, string verb)
    {
        var name = ReadEventItem(item, "name");
        return string.IsNullOrWhiteSpace(name) ? $"{verb} a tool" : $"{verb} {name}";
    }

    private static string ReadEventItem(JsonElement item, string name) =>
        item.ValueKind == JsonValueKind.Object &&
        item.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? string.Empty
            : string.Empty;

    private async void OnRunSelected(object? sender, RunSelectedEventArgs args)
    {
        try
        {
            ViewModel.RunDetails = "Loading run…";
            var result = await LoadRunReviewAsync(args.Run.FarmId);
            ViewModel.RunDetails = result.ToJsonString(JsonOptions);
        }
        catch (Exception exception)
        {
            ViewModel.RunDetails = exception.Message;
            ViewModel.Review.State = "Review unavailable";
            ShowInfo("Run review failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async Task<JsonObject> LoadRunReviewAsync(string farmId)
    {
        _currentReviewFarmId = farmId;
        ViewModel.Review.State = "Loading";
        ViewModel.Review.CanApply = false;
        ViewModel.Review.CanMerge = false;
        ViewModel.Review.CanRollback = false;
        var api = RequireApi();
        var resultTask = api.GetFarmAsync(farmId, _lifetime.Token);
        var changesTask = api.GetFarmChangeSetsAsync(farmId, _lifetime.Token);
        var checkpointsTask = api.GetFarmCheckpointsAsync(farmId, _lifetime.Token);
        await Task.WhenAll(resultTask, changesTask, checkpointsTask);

        _currentReviewResult = await resultTask;
        MainPageViewModel.Replace(ViewModel.CandidateChanges, (await changesTask).ChangeSets);
        MainPageViewModel.Replace(ViewModel.Checkpoints, (await checkpointsTask).Checkpoints);

        var decisionNode = _currentReviewResult["decision"] as JsonObject;
        var approvedWorker = decisionNode?["approved_worker"]?.GetValue<string?>();
        var decision = decisionNode?["decision"]?.GetValue<string?>() ?? "pending";
        var risk = decisionNode?["risk_level"]?.GetValue<string?>() ?? "not reported";
        var reason = decisionNode?["reason"]?.GetValue<string?>() ?? "No Supervisor rationale was recorded.";
        ViewModel.Review.Decision =
            $"Supervisor: {decision.Replace('_', ' ')} · Risk: {risk} · Approved worker: {approvedWorker ?? "none"}\n{reason}";
        var usage = _currentReviewResult["usage"] as JsonObject;
        ViewModel.Review.Cost =
            $"Supervisor: {FormatUsageBucket(usage?["supervisor"] as JsonObject)} | " +
            $"Workers: {FormatUsageBucket(usage?["workers"] as JsonObject)}\n" +
            FormatEconomics(usage?["economics"] as JsonObject);
        RunsSurface.Review.SelectInitial(approvedWorker);
        ViewModel.Review.State = _currentReviewResult["change_control"]?["status"]?.GetValue<string?>()
            ?? _currentReviewResult["status"]?.GetValue<string?>()
            ?? "Review";
        RefreshReviewActions();
        return _currentReviewResult;
    }

    private static string FormatUsageBucket(JsonObject? bucket)
    {
        if (bucket is null)
        {
            return "no usage yet";
        }
        var requests = bucket["request_count"]?.GetValue<int?>() ?? 0;
        var tokens = bucket["total_tokens"]?.GetValue<int?>() ?? 0;
        var cost = bucket["estimated_cost_usd"]?.GetValue<double?>() ?? 0;
        var unpriced = bucket["unpriced_requests"]?.GetValue<int?>() ?? 0;
        var costLabel = $"${cost:0.000000}" + (unpriced > 0 ? $" + {unpriced} unpriced" : string.Empty);
        return $"{requests} request(s), {tokens:N0} tokens, {costLabel}";
    }

    private static string FormatEconomics(JsonObject? economics)
    {
        if (economics is null)
        {
            return "Economics: unavailable";
        }
        var artifacts = economics["accepted_artifact_count"]?.GetValue<int?>() ?? 0;
        var perArtifact = economics["cost_per_accepted_artifact_usd"]?.GetValue<double?>();
        var savings = economics["estimated_savings_usd"]?.GetValue<double?>();
        var percent = economics["estimated_savings_percent"]?.GetValue<double?>();
        var artifactLabel = perArtifact is null
            ? $"{artifacts} accepted artifact(s), cost pending"
            : $"{artifacts} accepted artifact(s), ${perArtifact:0.000000} each";
        var savingsLabel = savings is null
            ? "premium comparison unavailable"
            : $"saved ${savings:0.000000} ({percent ?? 0:0.0}%) vs premium-only routing";
        return $"Economics: {artifactLabel} | {savingsLabel}";
    }

    private void OnReviewSelectionChanged(object? sender, EventArgs args) => RefreshReviewActions();

    private void RefreshReviewActions()
    {
        var selected = RunsSurface.Review.SelectedCandidate;
        var approvedWorker = _currentReviewResult?["decision"]?["approved_worker"]?.GetValue<string?>();
        var decision = _currentReviewResult?["decision"]?["decision"]?.GetValue<string?>();
        var state = _currentReviewResult?["change_control"]?["status"]?.GetValue<string?>();
        ViewModel.Review.CanApply = selected is not null
            && selected.MachineReview.Status == "passed"
            && decision == "approve_merge"
            && approvedWorker == selected.WorkerId
            && state is not ("APPLIED" or "VERIFIED" or "MERGED");
        ViewModel.Review.CanMerge = state == "VERIFIED";
        ViewModel.Review.CanRollback = RunsSurface.Review.SelectedCheckpoint is CheckpointSummary
        {
            Status: "APPLIED" or "VERIFIED" or "MERGED"
        };
    }

    private async void OnReviewApplyRequested(object? sender, EventArgs args)
    {
        if (_currentReviewFarmId is null || RunsSurface.Review.SelectedCandidate is not WorkerChangeSet candidate)
        {
            return;
        }
        if (!await ConfirmReviewActionAsync(
            "Apply and verify candidate?",
            $"Agent Farm will checkpoint the affected files, apply {candidate.DisplayName}, and run verification.",
            "Apply + verify"))
        {
            return;
        }
        ViewModel.Review.CanApply = false;
        try
        {
            await RequireApi().ApplyCandidateAsync(
                _currentReviewFarmId,
                candidate.WorkerId,
                _lifetime.Token);
            await LoadRunReviewAsync(_currentReviewFarmId);
            ShowInfo("Candidate verified", "The patch is applied and verification passed.", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            await LoadRunReviewAsync(_currentReviewFarmId);
            ShowInfo("Apply failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnReviewMergeRequested(object? sender, EventArgs args)
    {
        if (_currentReviewFarmId is null || !await ConfirmReviewActionAsync(
            "Merge verified candidate?",
            "This finalizes the Supervisor-approved candidate. The checkpoint remains available for rollback.",
            "Merge"))
        {
            return;
        }
        ViewModel.Review.CanMerge = false;
        try
        {
            await RequireApi().MergeCandidateAsync(_currentReviewFarmId, _lifetime.Token);
            await LoadRunReviewAsync(_currentReviewFarmId);
            ShowInfo("Candidate merged", "The verified change set is now accepted.", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            RefreshReviewActions();
            ShowInfo("Merge failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnReviewRollbackRequested(object? sender, EventArgs args)
    {
        if (_currentReviewFarmId is null || RunsSurface.Review.SelectedCheckpoint is not CheckpointSummary checkpoint)
        {
            return;
        }
        if (!await ConfirmReviewActionAsync(
            "Rollback checkpoint?",
            "Agent Farm will restore every affected file to its exact pre-apply state.",
            "Rollback"))
        {
            return;
        }
        ViewModel.Review.CanRollback = false;
        try
        {
            await RequireApi().RollbackCandidateAsync(
                _currentReviewFarmId,
                checkpoint.Id,
                false,
                _lifetime.Token);
            await LoadRunReviewAsync(_currentReviewFarmId);
            ShowInfo("Rollback complete", "The checkpoint state was restored.", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            var force = await ConfirmReviewActionAsync(
                "Files changed after apply",
                $"{exception.Message}\n\nForce rollback will overwrite those later edits.",
                "Force rollback");
            if (force)
            {
                try
                {
                    await RequireApi().RollbackCandidateAsync(
                        _currentReviewFarmId,
                        checkpoint.Id,
                        true,
                        _lifetime.Token);
                    await LoadRunReviewAsync(_currentReviewFarmId);
                    ShowInfo("Forced rollback complete", "The checkpoint state was restored.", InfoBarSeverity.Warning);
                    return;
                }
                catch (Exception forceException)
                {
                    ShowInfo("Rollback failed", forceException.Message, InfoBarSeverity.Error);
                }
            }
            RefreshReviewActions();
        }
    }

    private async Task<bool> ConfirmReviewActionAsync(
        string title,
        string message,
        string actionLabel)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = title,
            Content = message,
            PrimaryButtonText = actionLabel,
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    private async void OnSupervisorProviderChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_settingsUiUpdating || SettingsSurface.SupervisorProvider.SelectedValue is not string providerId)
        {
            return;
        }
        _settingsUiUpdating = true;
        SettingsSurface.SupervisorCustomModel.Text = string.Empty;
        SettingsSurface.SupervisorModel.SelectedIndex = -1;
        _settingsUiUpdating = false;
        await LoadSupervisorModelsAsync(providerId, string.Empty, false);
    }

    private void OnSupervisorModelChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_settingsUiUpdating)
        {
            return;
        }
        if (SettingsSurface.SupervisorModel.SelectedValue is string model)
        {
            _settingsUiUpdating = true;
            SettingsSurface.SupervisorCustomModel.Text = model;
            _settingsUiUpdating = false;
        }
        UpdateSupervisorReasoning();
    }

    private void OnSupervisorCustomModelChanged(object sender, TextChangedEventArgs e)
    {
        if (_settingsUiUpdating || SettingsSurface.SupervisorCustomModel.Visibility != Visibility.Visible)
        {
            return;
        }
        var customModel = SettingsSurface.SupervisorCustomModel.Text.Trim();
        if (SettingsSurface.SupervisorModel.SelectedValue is string selectedModel
            && !string.Equals(selectedModel, customModel, StringComparison.Ordinal))
        {
            _settingsUiUpdating = true;
            SettingsSurface.SupervisorModel.SelectedIndex = -1;
            _settingsUiUpdating = false;
        }
        UpdateSupervisorReasoning();
    }

    private async Task LoadSupervisorModelsAsync(string providerId, string currentModel, bool refresh)
    {
        var models = await LoadModelsAsync(providerId, currentModel, refresh);
        MainPageViewModel.Replace(ViewModel.SupervisorModels, models);
        SettingsSurface.SupervisorCustomModel.Visibility = IsManualModelProvider(providerId)
            ? Visibility.Visible
            : Visibility.Collapsed;
        _settingsUiUpdating = true;
        SettingsSurface.SupervisorModel.SelectedValue = currentModel;
        SettingsSurface.SupervisorCustomModel.Text = currentModel;
        _settingsUiUpdating = false;
        UpdateSupervisorReasoning();
    }

    private async void OnWorkerProfileSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_settingsUiUpdating)
        {
            CommitSelectedWorkerEditor();
        }
        _selectedWorkerProfile = SettingsSurface.WorkerProfiles.SelectedItem as WorkerProfileEditor;
        SettingsSurface.WorkerProfileEditor.DataContext = _selectedWorkerProfile;
        if (_selectedWorkerProfile is null)
        {
            return;
        }

        _settingsUiUpdating = true;
        SettingsSurface.WorkerHarness.SelectedValue = _selectedWorkerProfile.Harness;
        SettingsSurface.WorkerProvider.SelectedValue = _selectedWorkerProfile.Provider;
        SettingsSurface.WorkerCustomModel.Text = _selectedWorkerProfile.Model;
        _settingsUiUpdating = false;
        await LoadWorkerModelsAsync(_selectedWorkerProfile.Provider, _selectedWorkerProfile.Model, false);
        SetReasoningSelection(SettingsSurface.WorkerThinking, _selectedWorkerProfile.ReasoningMode);
        SetReasoningSelection(SettingsSurface.WorkerEffort, _selectedWorkerProfile.ReasoningEffort);
    }

    private async void OnWorkerProviderChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_settingsUiUpdating || _selectedWorkerProfile is null || SettingsSurface.WorkerProvider.SelectedValue is not string providerId)
        {
            return;
        }
        _selectedWorkerProfile.Provider = providerId;
        _selectedWorkerProfile.Model = string.Empty;
        SettingsSurface.WorkerCustomModel.Text = string.Empty;
        await LoadWorkerModelsAsync(providerId, string.Empty, false);
    }

    private void OnWorkerModelChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_settingsUiUpdating || _selectedWorkerProfile is null)
        {
            return;
        }
        if (SettingsSurface.WorkerModel.SelectedValue is string model)
        {
            _selectedWorkerProfile.Model = model;
            _settingsUiUpdating = true;
            SettingsSurface.WorkerCustomModel.Text = model;
            _settingsUiUpdating = false;
        }
        UpdateWorkerReasoning();
    }

    private void OnWorkerCustomModelChanged(object sender, TextChangedEventArgs e)
    {
        if (_settingsUiUpdating || _selectedWorkerProfile is null || SettingsSurface.WorkerCustomModel.Visibility != Visibility.Visible)
        {
            return;
        }
        var customModel = SettingsSurface.WorkerCustomModel.Text.Trim();
        _selectedWorkerProfile.Model = customModel;
        if (SettingsSurface.WorkerModel.SelectedValue is string selectedModel
            && !string.Equals(selectedModel, customModel, StringComparison.Ordinal))
        {
            _settingsUiUpdating = true;
            SettingsSurface.WorkerModel.SelectedIndex = -1;
            _settingsUiUpdating = false;
        }
        UpdateWorkerReasoning();
    }

    private async Task LoadWorkerModelsAsync(string providerId, string currentModel, bool refresh)
    {
        var models = await LoadModelsAsync(providerId, currentModel, refresh);
        MainPageViewModel.Replace(ViewModel.WorkerModels, models);
        SettingsSurface.WorkerCustomModel.Visibility = IsManualModelProvider(providerId)
            ? Visibility.Visible
            : Visibility.Collapsed;
        _settingsUiUpdating = true;
        SettingsSurface.WorkerModel.SelectedValue = currentModel;
        SettingsSurface.WorkerCustomModel.Text = currentModel;
        _settingsUiUpdating = false;
        UpdateWorkerReasoning();
    }

    private async Task<List<ModelOption>> LoadModelsAsync(string providerId, string currentModel, bool refresh)
    {
        var models = new List<ModelOption>();
        if (!string.IsNullOrWhiteSpace(providerId))
        {
            try
            {
                var catalog = await RequireApi().GetProviderModelsAsync(providerId, refresh, _lifetime.Token);
                models.AddRange(catalog.Models);
            }
            catch (Exception exception) when (exception is AgentFarmApiException or HttpRequestException or TaskCanceledException)
            {
                var template = FindProviderTemplate(providerId);
                models.AddRange(template?.Models ?? []);
            }
        }

        if (!string.IsNullOrWhiteSpace(currentModel) && models.All(item => item.Id != currentModel))
        {
            models.Insert(0, new ModelOption { Id = currentModel, Name = currentModel });
        }
        return models.DistinctBy(item => item.Id).OrderBy(item => item.Name).ToList();
    }

    private void UpdateSupervisorReasoning()
    {
        var providerId = SettingsSurface.SupervisorProvider.SelectedValue as string ?? string.Empty;
        var modelId = SettingsSurface.SupervisorModel.SelectedValue as string ?? SettingsSurface.SupervisorCustomModel.Text.Trim();
        var template = FindProviderTemplate(providerId);
        var capability = ViewModel.SupervisorModels.FirstOrDefault(item => item.Id == modelId)?.Reasoning;
        capability = HasReasoning(capability) ? capability : template?.Reasoning;
        if (template?.Custom == true && !HasReasoning(ViewModel.SupervisorModels.FirstOrDefault(item => item.Id == modelId)?.Reasoning))
        {
            capability = GatewayReasoning(modelId, capability);
        }
        ApplyReasoningOptions(
            ViewModel.SupervisorThinkingOptions,
            ViewModel.SupervisorEffortOptions,
            capability,
            SettingsSurface.SupervisorThinking,
            SettingsSurface.SupervisorEffort);
    }

    private void UpdateWorkerReasoning()
    {
        var providerId = SettingsSurface.WorkerProvider.SelectedValue as string ?? _selectedWorkerProfile?.Provider ?? string.Empty;
        var modelId = SettingsSurface.WorkerModel.SelectedValue as string ?? SettingsSurface.WorkerCustomModel.Text.Trim();
        var template = FindProviderTemplate(providerId);
        var capability = ViewModel.WorkerModels.FirstOrDefault(item => item.Id == modelId)?.Reasoning;
        capability = HasReasoning(capability) ? capability : template?.Reasoning;
        if (template?.Custom == true && !HasReasoning(ViewModel.WorkerModels.FirstOrDefault(item => item.Id == modelId)?.Reasoning))
        {
            capability = GatewayReasoning(modelId, capability);
        }
        ApplyReasoningOptions(
            ViewModel.WorkerThinkingOptions,
            ViewModel.WorkerEffortOptions,
            capability,
            SettingsSurface.WorkerThinking,
            SettingsSurface.WorkerEffort);
    }

    private static bool HasReasoning(ReasoningCapability? capability) =>
        capability is not null && (capability.Efforts.Count > 0 || capability.Thinking.Count > 0);

    private static ReasoningCapability? GatewayReasoning(string modelId, ReasoningCapability? fallback)
    {
        var lowered = modelId.ToLowerInvariant();
        if (lowered.Contains("qwen") || lowered.Contains("claude") || lowered.Contains("kimi") ||
            lowered.Contains("glm") || lowered.Contains("minimax") || lowered.Contains("magistral"))
        {
            return new ReasoningCapability { Thinking = ["enabled", "disabled"] };
        }
        if (lowered.Contains("deepseek"))
        {
            return new ReasoningCapability
            {
                Efforts = ["high", "max"],
                Thinking = ["enabled", "disabled"],
            };
        }
        if (lowered.Contains("gpt") || lowered.Contains("codex") || lowered.Contains("o1") ||
            lowered.Contains("o3") || lowered.Contains("o4"))
        {
            return new ReasoningCapability
            {
                Efforts = ["none", "default", "minimal", "low", "medium", "high", "xhigh", "max"],
            };
        }
        return fallback;
    }

    private void ApplyReasoningOptions(
        System.Collections.ObjectModel.ObservableCollection<string> thinkingTarget,
        System.Collections.ObjectModel.ObservableCollection<string> effortTarget,
        ReasoningCapability? capability,
        ComboBox thinkingCombo,
        ComboBox effortCombo)
    {
        var previousThinking = thinkingCombo.SelectedItem as string;
        var previousEffort = effortCombo.SelectedItem as string;
        MainPageViewModel.Replace(thinkingTarget, new[] { "Automatic" }.Concat(capability?.Thinking ?? []));
        MainPageViewModel.Replace(effortTarget, new[] { "Automatic" }.Concat(capability?.Efforts ?? []));
        thinkingCombo.IsEnabled = capability?.Thinking.Count > 0;
        effortCombo.IsEnabled = capability?.Efforts.Count > 0;
        thinkingCombo.SelectedItem = thinkingTarget.Contains(previousThinking ?? string.Empty) ? previousThinking : "Automatic";
        effortCombo.SelectedItem = effortTarget.Contains(previousEffort ?? string.Empty) ? previousEffort : "Automatic";
    }

    private ProviderTemplate? FindProviderTemplate(string providerId)
    {
        var configured = ViewModel.Providers.FirstOrDefault(item => item.Id == providerId);
        var templateId = configured?.Raw["template_id"]?.GetValue<string>();
        return ViewModel.ProviderTemplates.FirstOrDefault(item =>
            item.Id == providerId || item.Id == templateId ||
            (!string.IsNullOrWhiteSpace(configured?.BaseUrl) &&
             string.Equals(item.BaseUrl.TrimEnd('/'), configured.BaseUrl.TrimEnd('/'), StringComparison.OrdinalIgnoreCase)))
            ?? ViewModel.ProviderTemplates.FirstOrDefault(item => item.Id == "custom-openai-compatible");
    }

    private bool IsManualModelProvider(string providerId)
    {
        var template = FindProviderTemplate(providerId);
        return template is null || template.Custom;
    }

    private void OnAddWorkerProfile(object sender, RoutedEventArgs e)
    {
        var index = 1;
        string name;
        do
        {
            name = $"worker-{index++}";
        }
        while (ViewModel.WorkerProfiles.Any(item => item.Name == name));

        var provider = SettingsSurface.SupervisorProvider.SelectedValue as string ?? "openai";
        var profile = new WorkerProfileEditor
        {
            Name = name,
            DisplayName = $"Worker {index - 1}",
            Provider = provider,
            Model = string.Empty,
            TimeoutSeconds = 1800,
        };
        ViewModel.WorkerProfiles.Add(profile);
        SettingsSurface.WorkerProfiles.SelectedItem = profile;
        if (SettingsSurface.DefaultWorkerProfile.SelectedItem is null)
        {
            SettingsSurface.DefaultWorkerProfile.SelectedItem = profile;
        }
    }

    private async void OnRemoveWorkerProfile(object sender, RoutedEventArgs e)
    {
        if (_selectedWorkerProfile is null || ViewModel.WorkerProfiles.Count <= 1)
        {
            ShowInfo("Worker route required", "Keep at least one Worker route configured.", InfoBarSeverity.Warning);
            return;
        }
        if (!await ConfirmAsync("Remove Worker route?", $"Remove {_selectedWorkerProfile.DisplayName} from future plans?"))
        {
            return;
        }
        var index = SettingsSurface.WorkerProfiles.SelectedIndex;
        ViewModel.WorkerProfiles.Remove(_selectedWorkerProfile);
        SettingsSurface.WorkerProfiles.SelectedIndex = Math.Clamp(index, 0, ViewModel.WorkerProfiles.Count - 1);
    }

    private void OnProviderSelectionChanged(object sender, EventArgs e)
    {
        _selectedProvider = SettingsSurface.Providers.SelectedProvider as ProviderEditor;
        SettingsSurface.Providers.EditorDataContext = _selectedProvider;
        _settingsUiUpdating = true;
        SettingsSurface.Providers.ProviderId = _selectedProvider?.Id ?? string.Empty;
        SettingsSurface.Providers.WireApi = _selectedProvider?.WireApi ?? "chat";
        SettingsSurface.Providers.ApiKey = _selectedProvider?.ApiKey ?? string.Empty;
        _settingsUiUpdating = false;
    }

    private void OnProviderWireApiChanged(object sender, EventArgs e)
    {
        if (!_settingsUiUpdating && _selectedProvider is not null && SettingsSurface.Providers.WireApi is string wireApi)
        {
            _selectedProvider.WireApi = wireApi;
        }
    }

    private void OnProviderApiKeyChanged(object sender, RoutedEventArgs e)
    {
        if (!_settingsUiUpdating && _selectedProvider is not null)
        {
            _selectedProvider.ApiKey = SettingsSurface.Providers.ApiKey;
        }
    }

    private void OnAddProvider(object sender, RoutedEventArgs e)
    {
        if (SettingsSurface.Providers.SelectedTemplate is not ProviderTemplate template)
        {
            return;
        }

        var baseId = template.Custom ? "custom-provider" : template.Id;
        var id = baseId;
        var index = 2;
        while (ViewModel.Providers.Any(item => item.Id.Equals(id, StringComparison.OrdinalIgnoreCase)))
        {
            if (!template.Custom)
            {
                SettingsSurface.Providers.SelectedProvider = ViewModel.Providers.First(item => item.Id.Equals(id, StringComparison.OrdinalIgnoreCase));
                return;
            }
            id = $"{baseId}-{index++}";
        }

        var raw = new JsonObject
        {
            ["template_id"] = template.Id,
            ["name"] = template.Name,
            ["base_url"] = template.BaseUrl,
            ["env_key"] = template.EnvKey,
            ["wire_api"] = template.WireApi,
            ["requires_openai_auth"] = !string.IsNullOrWhiteSpace(template.EnvKey),
        };
        var provider = new ProviderEditor
        {
            Id = id,
            Name = template.Custom ? "Custom provider" : template.Name,
            BaseUrl = template.BaseUrl,
            EnvKey = template.EnvKey,
            WireApi = template.WireApi,
            Status = string.IsNullOrWhiteSpace(template.EnvKey) ? "Configured" : "Credential missing",
            Raw = raw,
        };
        ViewModel.Providers.Add(provider);
        RefreshProviderOptions();
        SettingsSurface.Providers.SelectedProvider = provider;
    }

    private async void OnRemoveProvider(object sender, RoutedEventArgs e)
    {
        if (_selectedProvider is null)
        {
            return;
        }
        if (ViewModel.WorkerProfiles.Any(item => item.Provider == _selectedProvider.Id) ||
            Equals(SettingsSurface.SupervisorProvider.SelectedValue, _selectedProvider.Id))
        {
            ShowInfo("Provider is in use", "Move the Supervisor and Worker routes to another provider first.", InfoBarSeverity.Warning);
            return;
        }
        if (!await ConfirmAsync("Remove provider?", $"Remove {_selectedProvider.DisplayName} from Agent Farm?"))
        {
            return;
        }
        ViewModel.Providers.Remove(_selectedProvider);
        RefreshProviderOptions();
        SettingsSurface.Providers.SelectedProviderIndex = ViewModel.Providers.Count > 0 ? 0 : -1;
    }

    private async void OnRefreshProviderModels(object sender, RoutedEventArgs e)
    {
        if (_selectedProvider is null)
        {
            return;
        }
        try
        {
            var catalog = await RequireApi().GetProviderModelsAsync(_selectedProvider.Id, true, _lifetime.Token);
            ShowInfo("Model list refreshed", $"Loaded {catalog.Models.Count} models for {_selectedProvider.DisplayName}.", InfoBarSeverity.Success);
            if (Equals(SettingsSurface.SupervisorProvider.SelectedValue, _selectedProvider.Id))
            {
                await LoadSupervisorModelsAsync(_selectedProvider.Id, SettingsSurface.SupervisorCustomModel.Text.Trim(), true);
            }
            if (_selectedWorkerProfile?.Provider == _selectedProvider.Id)
            {
                await LoadWorkerModelsAsync(_selectedProvider.Id, SettingsSurface.WorkerCustomModel.Text.Trim(), true);
            }
        }
        catch (Exception exception)
        {
            ShowInfo("Model refresh failed", exception.Message, InfoBarSeverity.Error);
        }
    }

    private bool TryRenameSelectedProvider(out ProviderRename? rename)
    {
        rename = null;
        if (_selectedProvider is null)
        {
            return true;
        }

        var oldId = _selectedProvider.Id;
        var newId = SettingsSurface.Providers.ProviderId.Trim();
        if (string.Equals(oldId, newId, StringComparison.Ordinal))
        {
            return true;
        }

        if (!ProviderIdPattern.IsMatch(newId))
        {
            SettingsSurface.Providers.ProviderId = oldId;
            ShowInfo(
                "Provider ID is invalid",
                "Use 1-64 characters: letters, numbers, dots, dashes, or underscores. The first character must be a letter or number.",
                InfoBarSeverity.Warning);
            return false;
        }

        if (ViewModel.Providers.Any(item =>
                !ReferenceEquals(item, _selectedProvider)
                && string.Equals(item.Id, newId, StringComparison.OrdinalIgnoreCase)))
        {
            SettingsSurface.Providers.ProviderId = oldId;
            ShowInfo("Provider ID already exists", $"Choose a different ID than {newId}.", InfoBarSeverity.Warning);
            return false;
        }

        _settingsUiUpdating = true;
        try
        {
            _selectedProvider.Id = newId;
            foreach (var profile in ViewModel.WorkerProfiles)
            {
                if (string.Equals(profile.Provider, oldId, StringComparison.OrdinalIgnoreCase))
                {
                    profile.Provider = newId;
                }
            }

            if (string.Equals(SettingsSurface.SupervisorProvider.SelectedValue as string, oldId, StringComparison.OrdinalIgnoreCase))
            {
                SettingsSurface.SupervisorProvider.SelectedValue = newId;
            }
            if (string.Equals(SettingsSurface.WorkerProvider.SelectedValue as string, oldId, StringComparison.OrdinalIgnoreCase))
            {
                SettingsSurface.WorkerProvider.SelectedValue = newId;
            }
        }
        finally
        {
            _settingsUiUpdating = false;
        }

        if (_settings?.Config["model_providers"] is JsonObject configuredProviders)
        {
            var rawProvider = configuredProviders[oldId];
            configuredProviders.Remove(oldId);
            if (rawProvider is not null)
            {
                configuredProviders[newId] = rawProvider;
            }
        }
        if (_settings?.Config is JsonObject config)
        {
            MigrateProviderReferences(config, oldId, newId);
        }

        SettingsSurface.Providers.ProviderId = newId;
        RefreshProviderOptions();
        rename = new ProviderRename(oldId, newId);
        return true;
    }

    private static void MigrateProviderReferences(JsonObject config, string oldId, string newId)
    {
        static string? Migrate(string? providerId, string sourceId, string targetId) =>
            string.Equals(providerId, sourceId, StringComparison.OrdinalIgnoreCase) ? targetId : providerId;

        static void MigrateField(JsonObject target, string key, string sourceId, string targetId)
        {
            var current = GetString(target, key);
            if (current is not null)
            {
                SetString(target, key, Migrate(current, sourceId, targetId));
            }
        }

        MigrateField(config, "supervisor_provider", oldId, newId);
        MigrateField(config, "worker_provider", oldId, newId);
        MigrateField(config, "worker_local_provider", oldId, newId);

        if (config["worker_profiles"] is JsonObject profiles)
        {
            foreach (var profile in profiles)
            {
                if (profile.Value is JsonObject profileConfig)
                {
                    MigrateField(profileConfig, "provider", oldId, newId);
                }
            }
        }
    }

    private async void OnSaveSettings(object sender, RoutedEventArgs e)
    {
        if (_settings is null)
        {
            return;
        }
        try
        {
            if (!TryRenameSelectedProvider(out var rename))
            {
                return;
            }
            SynchronizeRouteEditors();
            var config = (JsonObject)_settings.Config.DeepClone();
            if (rename is { } providerRename)
            {
                MigrateProviderReferences(config, providerRename.OldId, providerRename.NewId);
            }
            SetString(config, "supervisor_provider", SettingsSurface.SupervisorProvider.SelectedValue as string);
            SetString(config, "supervisor_harness", SettingsSurface.SupervisorHarness.SelectedValue as string);
            SetString(config, "supervisor_model", SelectedModel(SettingsSurface.SupervisorModel, SettingsSurface.SupervisorCustomModel));
            SetString(config, "supervisor_reasoning_mode", ReasoningValue(SettingsSurface.SupervisorThinking));
            SetString(config, "supervisor_reasoning_effort", ReasoningValue(SettingsSurface.SupervisorEffort));
            SetString(config, "default_worker_profile", SettingsSurface.DefaultWorkerProfile.SelectedValue as string);
            SetString(config, "worker_harness", SettingsSurface.WorkerHarness.SelectedValue as string);
            var overrides = config["codex_config_overrides"] as JsonObject ?? new JsonObject();
            if (SettingsSurface.NetworkAccess.IsChecked == true)
            {
                overrides["sandbox_workspace_write.network_access"] = true;
            }
            else
            {
                overrides.Remove("sandbox_workspace_write.network_access");
            }
            config["codex_config_overrides"] = overrides;
            SetOptionalNumber(config, "farm_budget_usd", SettingsSurface.FarmBudget.Value);
            SetOptionalNumber(config, "monthly_budget_usd", SettingsSurface.MonthlyBudget.Value);
            SetString(config, "budget_policy", SettingsSurface.BudgetPolicy.SelectedItem as string ?? "warn");
            config["budget_warning_ratio"] = double.IsNaN(SettingsSurface.BudgetWarningRatio.Value)
                ? 0.8
                : Math.Clamp(SettingsSurface.BudgetWarningRatio.Value, 0.01, 1);

            var profiles = new JsonObject();
            foreach (var profile in ViewModel.WorkerProfiles)
            {
                var raw = (JsonObject)profile.Raw.DeepClone();
                SetString(raw, "display_name", profile.DisplayName);
                SetString(raw, "harness", profile.Harness);
                SetString(raw, "provider", profile.Provider);
                SetString(raw, "model", profile.Model);
                SetString(raw, "reasoning_mode", profile.ReasoningMode);
                SetString(raw, "reasoning_effort", profile.ReasoningEffort);
                raw["timeout_seconds"] = profile.TimeoutSeconds;
                SetOptionalNumber(raw, "budget_usd", profile.BudgetUsd);
                SetString(raw, "capability_tier", profile.CapabilityTier);
                profiles[profile.Name] = raw;
            }
            config["worker_profiles"] = profiles;

            var providers = new JsonObject();
            var secrets = new Dictionary<string, string>();
            foreach (var provider in ViewModel.Providers)
            {
                var raw = (JsonObject)provider.Raw.DeepClone();
                SetString(raw, "name", provider.Name);
                SetString(raw, "base_url", provider.BaseUrl);
                SetString(raw, "env_key", provider.EnvKey);
                SetString(raw, "wire_api", provider.WireApi);
                providers[provider.Id] = raw;
                if (!string.IsNullOrWhiteSpace(provider.ApiKey))
                {
                    secrets[provider.Id] = provider.ApiKey;
                }
            }
            config["model_providers"] = providers;

            var saved = await RequireApi().SaveSettingsAsync(new SettingsSaveRequest
            {
                Config = config,
                ProviderSecrets = secrets,
            }, _lifetime.Token);
            ApplySettings(saved);
            await RefreshBootstrapAsync();
            ShowInfo("Settings saved", "New Supervisor and Worker runs will use these native routes.", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowInfo("Settings could not be saved", exception.Message, InfoBarSeverity.Error);
        }
    }

    private void SynchronizeRouteEditors()
    {
        CommitSelectedWorkerEditor();
        if (_selectedProvider is not null)
        {
            _selectedProvider.WireApi = SettingsSurface.Providers.WireApi as string ?? _selectedProvider.WireApi;
            _selectedProvider.ApiKey = SettingsSurface.Providers.ApiKey;
        }
    }

    private void CommitSelectedWorkerEditor()
    {
        if (_selectedWorkerProfile is null)
        {
            return;
        }
        _selectedWorkerProfile.Provider = SettingsSurface.WorkerProvider.SelectedValue as string ?? _selectedWorkerProfile.Provider;
        _selectedWorkerProfile.Harness = SettingsSurface.WorkerHarness.SelectedValue as string ?? _selectedWorkerProfile.Harness;
        _selectedWorkerProfile.Model = SelectedModel(SettingsSurface.WorkerModel, SettingsSurface.WorkerCustomModel);
        _selectedWorkerProfile.ReasoningMode = ReasoningValue(SettingsSurface.WorkerThinking) ?? string.Empty;
        _selectedWorkerProfile.ReasoningEffort = ReasoningValue(SettingsSurface.WorkerEffort) ?? string.Empty;
    }

    private async Task RefreshBootstrapAsync()
    {
        ApplyBootstrap(await RequireApi().GetBootstrapAsync(_lifetime.Token));
    }

    private void SetPlanningState(bool planning, string label)
    {
        _planning = planning;
        WorkspaceSurface.ComposerView.SetPlanningState(planning, label);
        if (planning)
        {
            ViewModel.Execution.BeginPlanning("Supervisor starting");
        }
    }

    private void ShowInfo(string title, string message, InfoBarSeverity severity)
    {
        WorkspaceSurface.InfoBar.Title = title;
        WorkspaceSurface.InfoBar.Message = message;
        WorkspaceSurface.InfoBar.Severity = severity;
        WorkspaceSurface.InfoBar.IsOpen = true;
        var shouldShowSystem = severity == InfoBarSeverity.Error
            || title.Contains("completed", StringComparison.OrdinalIgnoreCase)
            || title.Contains("failed", StringComparison.OrdinalIgnoreCase)
            || title.Contains("update available", StringComparison.OrdinalIgnoreCase);
        _notifications.Enqueue(title, message, severity.ToString(), shouldShowSystem);
    }

    private void ShowStartupError(string message, bool degraded = false)
    {
        if (degraded)
        {
            ViewModel.RuntimeState.SetDegraded(message);
        }
        else
        {
            ViewModel.RuntimeState.SetOffline(message);
        }
        StartupError.Message = message;
        StartupError.IsOpen = true;
    }

    private async Task<bool> ConfirmAsync(string title, string message)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = title,
            Content = message,
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    private AgentFarmApiClient RequireApi() =>
        _api ?? throw new InvalidOperationException("The local Agent Farm runtime is not connected.");

    private static string SelectedModel(ComboBox combo, TextBox customBox)
    {
        var customModel = customBox.Text.Trim();
        return customBox.Visibility == Visibility.Visible && customModel.Length > 0
            ? customModel
            : combo.SelectedValue as string ?? customModel;
    }

    private static string? ReasoningValue(ComboBox combo) =>
        combo.SelectedItem is string value && value != "Automatic" ? value : null;

    private static void SetReasoningSelection(ComboBox combo, string? value)
    {
        combo.SelectedItem = string.IsNullOrWhiteSpace(value) ? "Automatic" : value;
    }

    private static string? GetString(JsonObject? value, string key) =>
        value?[key]?.GetValue<string?>();

    private static int? GetInteger(JsonObject? value, string key) =>
        value?[key]?.GetValue<int?>();

    private static double? GetDouble(JsonObject? value, string key) =>
        value?[key]?.GetValue<double?>();

    private static bool GetBoolean(JsonObject? value, string key) =>
        value?[key]?.GetValue<bool?>() ?? false;

    private static void SetString(JsonObject target, string key, string? value)
    {
        target[key] = string.IsNullOrWhiteSpace(value) ? null : value;
    }

    private static void SetOptionalNumber(JsonObject target, string key, double value)
    {
        target[key] = double.IsNaN(value) || value <= 0 ? null : value;
    }

    private static string? ReadPayloadString(JsonElement payload, string name) =>
        payload.ValueKind == JsonValueKind.Object &&
        payload.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static List<string> ReadAttachmentNames(JsonElement payload)
    {
        var names = new List<string>();
        if (payload.ValueKind != JsonValueKind.Object ||
            !payload.TryGetProperty("attachments", out var attachments) ||
            attachments.ValueKind != JsonValueKind.Array)
        {
            return names;
        }

        foreach (var attachment in attachments.EnumerateArray())
        {
            if (attachment.ValueKind == JsonValueKind.Object &&
                attachment.TryGetProperty("name", out var name) &&
                name.ValueKind == JsonValueKind.String &&
                name.GetString() is { Length: > 0 } value)
            {
                names.Add(value);
            }
        }
        return names;
    }

    private static string? ReadNestedPayloadString(JsonElement payload, string parent, string child) =>
        payload.ValueKind == JsonValueKind.Object &&
        payload.TryGetProperty(parent, out var parentValue) &&
        parentValue.ValueKind == JsonValueKind.Object &&
        parentValue.TryGetProperty(child, out var childValue) &&
        childValue.ValueKind == JsonValueKind.String
            ? childValue.GetString()
            : null;
}
