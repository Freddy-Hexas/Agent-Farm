using AgentFarm_Desktop.Models;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System.Collections.ObjectModel;

namespace AgentFarm_Desktop.ViewModels;

public partial class MainPageViewModel : ObservableObject
{
    public MainPageViewModel()
    {
        ClearNotificationsCommand = new RelayCommand(Notifications.Clear);
    }
    public ShellViewModel Shell { get; } = new();
    public ExecutionViewModel Execution { get; } = new();
    public ReviewViewModel Review { get; } = new();
    public SettingsViewModel Settings { get; } = new();
    public RuntimeStateViewModel RuntimeState { get; } = new();
    public IRelayCommand ShowWorkspaceCommand => Shell.ShowWorkspaceCommand;
    public IRelayCommand ShowRunsCommand => Shell.ShowRunsCommand;
    public IRelayCommand ShowSettingsCommand => Shell.ShowSettingsCommand;
    public IRelayCommand ToggleNavigationPaneCommand => Shell.ToggleNavigationPaneCommand;
    public IRelayCommand ShowAgentRoutesCommand => Settings.ShowAgentRoutesCommand;
    public IRelayCommand ShowProvidersCommand => Settings.ShowProvidersCommand;
    public IRelayCommand ClearNotificationsCommand { get; }
    public ObservableCollection<ThreadSummary> Threads { get; } = [];
    public ObservableCollection<ThreadSummary> ThreadCatalog { get; } = [];
    public ObservableCollection<TimelineEntry> Timeline { get; } = [];
    public ObservableCollection<LiveAgentOutput> LiveAgents { get; } = [];
    public ObservableCollection<ApprovalRequest> PendingApprovals { get; } = [];
    public ObservableCollection<AttachmentItem> Attachments { get; } = [];
    public ObservableCollection<WorkerPlanItem> PlannedWorkers { get; } = [];
    public ObservableCollection<FarmSummary> Runs { get; } = [];
    public ObservableCollection<WorkerChangeSet> CandidateChanges { get; } = [];
    public ObservableCollection<CheckpointSummary> Checkpoints { get; } = [];
    public ObservableCollection<WorkerProfileSummary> ActiveProfiles { get; } = [];
    public ObservableCollection<ProviderOption> ProviderOptions { get; } = [];
    public ObservableCollection<ModelOption> SupervisorModels { get; } = [];
    public ObservableCollection<ModelOption> WorkerModels { get; } = [];
    public ObservableCollection<WorkerProfileEditor> WorkerProfiles { get; } = [];
    public ObservableCollection<ProviderEditor> Providers { get; } = [];
    public ObservableCollection<ProviderTemplate> ProviderTemplates { get; } = [];
    public ObservableCollection<NotificationItem> Notifications { get; } = [];
    public ObservableCollection<string> SupervisorThinkingOptions { get; } = [];
    public ObservableCollection<string> SupervisorEffortOptions { get; } = [];
    public ObservableCollection<string> WorkerThinkingOptions { get; } = [];
    public ObservableCollection<string> WorkerEffortOptions { get; } = [];

    [ObservableProperty]
    public partial string RepositoryName { get; set; } = "Starting…";

    [ObservableProperty]
    public partial string RepositoryPath { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string Branch { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string SupervisorRoute { get; set; } = "Not configured";

    [ObservableProperty]
    public partial string SupervisorMode { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string CurrentTitle { get; set; } = "New task";

    [ObservableProperty]
    public partial string CurrentSubtitle { get; set; } = "Describe an outcome. The Supervisor will plan before Workers execute.";

    [ObservableProperty]
    public partial string RunDetails { get; set; } = "Select a run to inspect its result.";

    [ObservableProperty]
    public partial string SettingsPath { get; set; } = string.Empty;

    [ObservableProperty]
    public partial string DiagnosticBundlePath { get; set; } = "No diagnostic bundle exported.";

    [ObservableProperty]
    public partial string UpdateStatus { get; set; } = "Updates are checked automatically.";

    [ObservableProperty]
    public partial string ThreadQuery { get; set; } = string.Empty;

    public void ApplyBootstrap(BootstrapResponse bootstrap)
    {
        RepositoryName = bootstrap.Repository.Name;
        RepositoryPath = bootstrap.Repository.Path;
        Branch = bootstrap.Repository.Branch;
        SupervisorRoute = $"{bootstrap.Supervisor.Provider} · {bootstrap.Supervisor.Model}";
        SupervisorMode = bootstrap.Supervisor.Mode;

        Replace(ThreadCatalog, bootstrap.Threads);
        ApplyThreadFilter();
        Replace(Runs, bootstrap.Farms);
        Replace(ActiveProfiles, bootstrap.Profiles);
    }

    public static void Replace<T>(ObservableCollection<T> target, IEnumerable<T> source)
    {
        target.Clear();
        foreach (var item in source)
        {
            target.Add(item);
        }
    }

    public void ApplyThreadFilter()
    {
        var query = ThreadQuery.Trim();
        Replace(
            Threads,
            ThreadCatalog.Where(thread => string.IsNullOrEmpty(query)
                || thread.Title.Contains(query, StringComparison.CurrentCultureIgnoreCase)
                || thread.Status.Contains(query, StringComparison.OrdinalIgnoreCase)));
    }
}
