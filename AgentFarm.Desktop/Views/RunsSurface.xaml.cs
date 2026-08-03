using AgentFarm_Desktop.Models;
using AgentFarm_Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Collections.ObjectModel;
using System.Collections.Specialized;

namespace AgentFarm_Desktop.Views;

public sealed class RunSelectedEventArgs(FarmSummary run) : EventArgs
{
    public FarmSummary Run { get; } = run;
}

public sealed partial class RunsSurface : UserControl
{
    public static readonly DependencyProperty RunsProperty = DependencyProperty.Register(nameof(Runs), typeof(ObservableCollection<FarmSummary>), typeof(RunsSurface), new PropertyMetadata(null, OnRunsChanged));
    public static readonly DependencyProperty CandidatesProperty = DependencyProperty.Register(nameof(Candidates), typeof(ObservableCollection<WorkerChangeSet>), typeof(RunsSurface), new PropertyMetadata(null));
    public static readonly DependencyProperty CheckpointsProperty = DependencyProperty.Register(nameof(Checkpoints), typeof(ObservableCollection<CheckpointSummary>), typeof(RunsSurface), new PropertyMetadata(null));
    public static readonly DependencyProperty ReviewViewModelProperty = DependencyProperty.Register(nameof(ReviewViewModel), typeof(ReviewViewModel), typeof(RunsSurface), new PropertyMetadata(null));

    public RunsSurface() => InitializeComponent();
    private ObservableCollection<FarmSummary>? _runs;
    public event EventHandler<RunSelectedEventArgs>? RunSelected;
    public ObservableCollection<FarmSummary>? Runs { get => (ObservableCollection<FarmSummary>?)GetValue(RunsProperty); set => SetValue(RunsProperty, value); }
    public ObservableCollection<WorkerChangeSet>? Candidates { get => (ObservableCollection<WorkerChangeSet>?)GetValue(CandidatesProperty); set => SetValue(CandidatesProperty, value); }
    public ObservableCollection<CheckpointSummary>? Checkpoints { get => (ObservableCollection<CheckpointSummary>?)GetValue(CheckpointsProperty); set => SetValue(CheckpointsProperty, value); }
    public ReviewViewModel ReviewViewModel { get => (ReviewViewModel)GetValue(ReviewViewModelProperty); set => SetValue(ReviewViewModelProperty, value); }
    public ReviewSurface Review => ReviewControl;
    public void FocusPrimary() => RunsList.Focus(FocusState.Programmatic);

    private static void OnRunsChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) =>
        ((RunsSurface)sender).ObserveRuns(args.NewValue as ObservableCollection<FarmSummary>);

    private void ObserveRuns(ObservableCollection<FarmSummary>? runs)
    {
        if (_runs is not null)
        {
            _runs.CollectionChanged -= OnRunsCollectionChanged;
        }
        _runs = runs;
        RunsList.ItemsSource = runs;
        if (_runs is not null)
        {
            _runs.CollectionChanged += OnRunsCollectionChanged;
        }
        UpdateEmptyState();
    }

    private void OnRunsCollectionChanged(object? sender, NotifyCollectionChangedEventArgs args) => UpdateEmptyState();

    private void UpdateEmptyState()
    {
        var empty = (_runs?.Count ?? 0) == 0;
        RunsEmptyState.Visibility = empty ? Visibility.Visible : Visibility.Collapsed;
        RunsList.Visibility = empty ? Visibility.Collapsed : Visibility.Visible;
    }
    private void OnRunSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (RunsList.SelectedItem is FarmSummary run) RunSelected?.Invoke(this, new RunSelectedEventArgs(run));
    }
}
