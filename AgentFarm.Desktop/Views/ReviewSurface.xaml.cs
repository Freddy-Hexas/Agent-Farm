using AgentFarm_Desktop.Models;
using AgentFarm_Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Collections.ObjectModel;

namespace AgentFarm_Desktop.Views;

public sealed partial class ReviewSurface : UserControl
{
    public static readonly DependencyProperty ViewModelProperty = DependencyProperty.Register(nameof(ViewModel), typeof(ReviewViewModel), typeof(ReviewSurface), new PropertyMetadata(null));
    public static readonly DependencyProperty CandidatesProperty = DependencyProperty.Register(nameof(Candidates), typeof(ObservableCollection<WorkerChangeSet>), typeof(ReviewSurface), new PropertyMetadata(null, OnCandidatesChanged));
    public static readonly DependencyProperty CheckpointsProperty = DependencyProperty.Register(nameof(Checkpoints), typeof(ObservableCollection<CheckpointSummary>), typeof(ReviewSurface), new PropertyMetadata(null, OnCheckpointsChanged));

    public ReviewSurface() => InitializeComponent();

    public event EventHandler? SelectionChanged;

    public ObservableCollection<WorkerChangeSet>? Candidates { get => (ObservableCollection<WorkerChangeSet>?)GetValue(CandidatesProperty); set => SetValue(CandidatesProperty, value); }
    public ObservableCollection<CheckpointSummary>? Checkpoints { get => (ObservableCollection<CheckpointSummary>?)GetValue(CheckpointsProperty); set => SetValue(CheckpointsProperty, value); }
    public ReviewViewModel ViewModel { get => (ReviewViewModel)GetValue(ViewModelProperty); set => SetValue(ViewModelProperty, value); }
    public WorkerChangeSet? SelectedCandidate => CandidateACombo.SelectedItem as WorkerChangeSet;
    public CheckpointSummary? SelectedCheckpoint => CheckpointCombo.SelectedItem as CheckpointSummary;

    public void SelectInitial(string? approvedWorker)
    {
        CandidateACombo.SelectedItem = Candidates?.FirstOrDefault(candidate => candidate.WorkerId == approvedWorker) ?? Candidates?.FirstOrDefault();
        CandidateBCombo.SelectedItem = Candidates?.FirstOrDefault(candidate => candidate != CandidateACombo.SelectedItem);
        CheckpointCombo.SelectedItem = Checkpoints?.FirstOrDefault();
        RefreshDiffs();
    }

    public void RefreshDiffs()
    {
        var candidateA = CandidateACombo.SelectedItem as WorkerChangeSet;
        var candidateB = CandidateBCombo.SelectedItem as WorkerChangeSet;
        DiffA.Text = candidateA?.UnifiedDiff ?? "No candidate selected.";
        SummaryA.Text = candidateA?.EvidenceSummary ?? string.Empty;
        DiffB.Text = candidateB?.UnifiedDiff ?? "No second candidate available.";
        SummaryB.Text = candidateB?.EvidenceSummary ?? string.Empty;
    }

    private static void OnCandidatesChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var surface = (ReviewSurface)sender;
        surface.CandidateACombo.ItemsSource = args.NewValue;
        surface.CandidateBCombo.ItemsSource = args.NewValue;
    }
    private static void OnCheckpointsChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ReviewSurface)sender).CheckpointCombo.ItemsSource = args.NewValue;
    private void OnCandidateChanged(object sender, SelectionChangedEventArgs args) { RefreshDiffs(); SelectionChanged?.Invoke(this, EventArgs.Empty); }
    private void OnCheckpointChanged(object sender, SelectionChangedEventArgs args) => SelectionChanged?.Invoke(this, EventArgs.Empty);
}
