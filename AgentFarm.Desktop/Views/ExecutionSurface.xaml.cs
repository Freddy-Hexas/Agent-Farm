using AgentFarm_Desktop.Models;
using AgentFarm_Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using System.Collections.ObjectModel;
using System.Collections.Specialized;

namespace AgentFarm_Desktop.Views;

public sealed class ExecutionApprovalEventArgs(string approvalId, string decision) : EventArgs
{
    public string ApprovalId { get; } = approvalId;
    public string Decision { get; } = decision;
}

public sealed class ExecutionWorkerEventArgs(string workerId) : EventArgs
{
    public string WorkerId { get; } = workerId;
}

public sealed partial class ExecutionSurface : UserControl
{
    public static readonly DependencyProperty ViewModelProperty = DependencyProperty.Register(nameof(ViewModel), typeof(ExecutionViewModel), typeof(ExecutionSurface), new PropertyMetadata(null));
    public static readonly DependencyProperty SupervisorRouteProperty = DependencyProperty.Register(nameof(SupervisorRoute), typeof(string), typeof(ExecutionSurface), new PropertyMetadata(string.Empty, OnSupervisorRouteChanged));
    public static readonly DependencyProperty SupervisorModeProperty = DependencyProperty.Register(nameof(SupervisorMode), typeof(string), typeof(ExecutionSurface), new PropertyMetadata(string.Empty, OnSupervisorModeChanged));
    public static readonly DependencyProperty PlannedWorkersProperty = DependencyProperty.Register(nameof(PlannedWorkers), typeof(ObservableCollection<WorkerPlanItem>), typeof(ExecutionSurface), new PropertyMetadata(null, OnPlannedWorkersChanged));
    public static readonly DependencyProperty LiveAgentsProperty = DependencyProperty.Register(nameof(LiveAgents), typeof(ObservableCollection<LiveAgentOutput>), typeof(ExecutionSurface), new PropertyMetadata(null, OnLiveAgentsChanged));
    public static readonly DependencyProperty PendingApprovalsProperty = DependencyProperty.Register(nameof(PendingApprovals), typeof(ObservableCollection<ApprovalRequest>), typeof(ExecutionSurface), new PropertyMetadata(null, OnPendingApprovalsChanged));

    private ObservableCollection<LiveAgentOutput>? _liveAgents;
    private ObservableCollection<ApprovalRequest>? _pendingApprovals;

    public ExecutionSurface() => InitializeComponent();

    public event EventHandler<ExecutionApprovalEventArgs>? ApprovalRequested;
    public event EventHandler<ExecutionWorkerEventArgs>? WorkerCancelRequested;
    public event EventHandler<ExecutionWorkerEventArgs>? WorkerRetryRequested;

    public string SupervisorRoute { get => (string)GetValue(SupervisorRouteProperty); set => SetValue(SupervisorRouteProperty, value); }
    public ExecutionViewModel ViewModel { get => (ExecutionViewModel)GetValue(ViewModelProperty); set => SetValue(ViewModelProperty, value); }
    public string SupervisorMode { get => (string)GetValue(SupervisorModeProperty); set => SetValue(SupervisorModeProperty, value); }
    public ObservableCollection<WorkerPlanItem>? PlannedWorkers { get => (ObservableCollection<WorkerPlanItem>?)GetValue(PlannedWorkersProperty); set => SetValue(PlannedWorkersProperty, value); }
    public ObservableCollection<LiveAgentOutput>? LiveAgents { get => (ObservableCollection<LiveAgentOutput>?)GetValue(LiveAgentsProperty); set => SetValue(LiveAgentsProperty, value); }
    public ObservableCollection<ApprovalRequest>? PendingApprovals { get => (ObservableCollection<ApprovalRequest>?)GetValue(PendingApprovalsProperty); set => SetValue(PendingApprovalsProperty, value); }

    public void ShowPlan() => SectionSelector.SelectedItem = PlanItem;
    public void ShowLive() => SectionSelector.SelectedItem = LiveItem;
    public void FocusPrimary() => SectionSelector.Focus(FocusState.Programmatic);
    public void RefreshEmptyState() => LiveEmptyState.Visibility = (_liveAgents?.Count ?? 0) == 0 && (_pendingApprovals?.Count ?? 0) == 0 ? Visibility.Visible : Visibility.Collapsed;
    public void ScrollTo(LiveAgentOutput item) => AgentsList.ScrollIntoView(item);

    private static void OnSupervisorRouteChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ExecutionSurface)sender).SupervisorRouteLabel.Text = args.NewValue as string ?? string.Empty;
    private static void OnSupervisorModeChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ExecutionSurface)sender).SupervisorModeLabel.Text = args.NewValue as string ?? string.Empty;
    private static void OnPlannedWorkersChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ExecutionSurface)sender).PlanList.ItemsSource = args.NewValue;
    private static void OnLiveAgentsChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ExecutionSurface)sender).ObserveLiveAgents(args.NewValue as ObservableCollection<LiveAgentOutput>);
    private static void OnPendingApprovalsChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ExecutionSurface)sender).ObserveApprovals(args.NewValue as ObservableCollection<ApprovalRequest>);

    private void ObserveLiveAgents(ObservableCollection<LiveAgentOutput>? items)
    {
        if (_liveAgents is not null) _liveAgents.CollectionChanged -= OnCollectionChanged;
        _liveAgents = items;
        AgentsList.ItemsSource = items;
        if (_liveAgents is not null) _liveAgents.CollectionChanged += OnCollectionChanged;
        RefreshEmptyState();
    }

    private void ObserveApprovals(ObservableCollection<ApprovalRequest>? items)
    {
        if (_pendingApprovals is not null) _pendingApprovals.CollectionChanged -= OnCollectionChanged;
        _pendingApprovals = items;
        ApprovalsList.ItemsSource = items;
        if (_pendingApprovals is not null) _pendingApprovals.CollectionChanged += OnCollectionChanged;
        RefreshEmptyState();
    }

    private void OnCollectionChanged(object? sender, NotifyCollectionChangedEventArgs args) => RefreshEmptyState();
    private void OnSectionChanged(SelectorBar sender, SelectorBarSelectionChangedEventArgs args)
    {
        var live = sender.SelectedItem == LiveItem;
        PlanList.Visibility = live ? Visibility.Collapsed : Visibility.Visible;
        LivePanel.Visibility = live ? Visibility.Visible : Visibility.Collapsed;
    }
    private void OnCancelWorker(object sender, RoutedEventArgs args)
    {
        if (sender is Button { Tag: string workerId }) WorkerCancelRequested?.Invoke(this, new ExecutionWorkerEventArgs(workerId));
    }
    private void OnRetryWorker(object sender, RoutedEventArgs args)
    {
        if (sender is Button { Tag: string workerId }) WorkerRetryRequested?.Invoke(this, new ExecutionWorkerEventArgs(workerId));
    }
    private void RaiseApproval(object sender, string decision)
    {
        if (sender is Button { Tag: string approvalId })
        {
            ApprovalRequested?.Invoke(this, new ExecutionApprovalEventArgs(approvalId, decision));
        }
    }
    private void OnApprovalAllowOnce(object sender, RoutedEventArgs args) => RaiseApproval(sender, "allow_once");
    private void OnApprovalAllowSession(object sender, RoutedEventArgs args) => RaiseApproval(sender, "allow_session");
    private void OnApprovalDeny(object sender, RoutedEventArgs args) => RaiseApproval(sender, "deny");
    private void OnApprovalCancel(object sender, RoutedEventArgs args) => RaiseApproval(sender, "cancel");
}
