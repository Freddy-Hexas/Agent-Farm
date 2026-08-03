using AgentFarm_Desktop.Models;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using System.Collections.ObjectModel;
using System.Collections.Specialized;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.System;

namespace AgentFarm_Desktop.Views;

public sealed class ComposerFilesEventArgs(IReadOnlyList<StorageFile> files) : EventArgs
{
    public IReadOnlyList<StorageFile> Files { get; } = files;
}

public sealed class ComposerAttachmentEventArgs(string attachmentId) : EventArgs
{
    public string AttachmentId { get; } = attachmentId;
}

public sealed class ComposerErrorEventArgs(Exception exception) : EventArgs
{
    public Exception Exception { get; } = exception;
}

public sealed partial class ComposerSurface : UserControl
{
    public static readonly DependencyProperty RepositoryNameProperty = DependencyProperty.Register(
        nameof(RepositoryName), typeof(string), typeof(ComposerSurface),
        new PropertyMetadata(string.Empty, OnRepositoryNameChanged));
    public static readonly DependencyProperty SupervisorRouteProperty = DependencyProperty.Register(
        nameof(SupervisorRoute), typeof(string), typeof(ComposerSurface),
        new PropertyMetadata(string.Empty, OnSupervisorRouteChanged));
    public static readonly DependencyProperty AttachmentsProperty = DependencyProperty.Register(
        nameof(Attachments), typeof(ObservableCollection<AttachmentItem>), typeof(ComposerSurface),
        new PropertyMetadata(null, OnAttachmentsChanged));

    private ObservableCollection<AttachmentItem>? _observedAttachments;
    private bool _isPlanning;

    public ComposerSurface()
    {
        InitializeComponent();
        UpdateWorkerCountLabel();
        UpdateAttachmentTray();
    }

    public event EventHandler? PlanRequested;
    public event EventHandler? AttachFilesRequested;
    public event EventHandler? ConfigureRoutingRequested;
    public event EventHandler<ComposerFilesEventArgs>? FilesDropped;
    public event EventHandler<ComposerAttachmentEventArgs>? AttachmentRemovalRequested;
    public event EventHandler<ComposerErrorEventArgs>? DropFailed;

    public string RepositoryName
    {
        get => (string)GetValue(RepositoryNameProperty);
        set => SetValue(RepositoryNameProperty, value);
    }

    public string SupervisorRoute
    {
        get => (string)GetValue(SupervisorRouteProperty);
        set => SetValue(SupervisorRouteProperty, value);
    }

    public ObservableCollection<AttachmentItem>? Attachments
    {
        get => (ObservableCollection<AttachmentItem>?)GetValue(AttachmentsProperty);
        set => SetValue(AttachmentsProperty, value);
    }

    public string PromptText
    {
        get => PromptBox.Text;
        set => PromptBox.Text = value;
    }

    public int WorkerCount => (int)Math.Clamp(
        double.IsNaN(WorkerCountBox.Value) ? 3 : WorkerCountBox.Value,
        WorkerCountBox.Minimum,
        WorkerCountBox.Maximum);

    public double MaximumWorkers
    {
        get => WorkerCountBox.Maximum;
        set
        {
            WorkerCountBox.Maximum = Math.Max(1, value);
            WorkerCountBox.Value = Math.Min(WorkerCountBox.Value, WorkerCountBox.Maximum);
        }
    }

    public string BaseReference => string.IsNullOrWhiteSpace(BaseRefBox.Text)
        ? "HEAD"
        : BaseRefBox.Text.Trim();

    public void FocusPrompt() => PromptBox.Focus(FocusState.Programmatic);

    public void SetPlanningState(bool planning, string label)
    {
        _isPlanning = planning;
        PlanButton.IsEnabled = !planning;
        AttachFilesButton.IsEnabled = !planning;
        AttachmentTray.IsEnabled = !planning;
        PlanButtonIcon.Visibility = planning ? Visibility.Collapsed : Visibility.Visible;
        PlanProgressRing.Visibility = planning ? Visibility.Visible : Visibility.Collapsed;
        PlanProgressRing.IsActive = planning;
        AutomationProperties.SetName(PlanButton, label);
        ToolTipService.SetToolTip(PlanButton, planning ? label : "Plan task (Ctrl+Enter)");
    }

    private static void OnRepositoryNameChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        if (sender is ComposerSurface surface)
        {
            surface.RepositoryLabel.Text = args.NewValue as string ?? string.Empty;
        }
    }

    private static void OnSupervisorRouteChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        if (sender is ComposerSurface surface)
        {
            surface.RouteLabel.Text = args.NewValue as string ?? string.Empty;
        }
    }

    private static void OnAttachmentsChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        if (sender is ComposerSurface surface)
        {
            surface.ObserveAttachments(args.NewValue as ObservableCollection<AttachmentItem>);
        }
    }

    private void ObserveAttachments(ObservableCollection<AttachmentItem>? attachments)
    {
        if (_observedAttachments is not null)
        {
            _observedAttachments.CollectionChanged -= OnAttachmentsCollectionChanged;
        }
        _observedAttachments = attachments;
        AttachmentRepeater.ItemsSource = attachments;
        if (_observedAttachments is not null)
        {
            _observedAttachments.CollectionChanged += OnAttachmentsCollectionChanged;
        }
        UpdateAttachmentTray();
    }

    private void OnAttachmentsCollectionChanged(object? sender, NotifyCollectionChangedEventArgs args) => UpdateAttachmentTray();

    private void UpdateAttachmentTray() => AttachmentTray.Visibility =
        _observedAttachments is null || _observedAttachments.Count == 0
            ? Visibility.Collapsed
            : Visibility.Visible;

    private void OnPlan(object sender, RoutedEventArgs args) => PlanRequested?.Invoke(this, EventArgs.Empty);

    private void OnAttachFiles(object sender, RoutedEventArgs args) => AttachFilesRequested?.Invoke(this, EventArgs.Empty);

    private void OnConfigureRouting(object sender, RoutedEventArgs args) => ConfigureRoutingRequested?.Invoke(this, EventArgs.Empty);

    private void OnOpenTaskOptions(object sender, RoutedEventArgs args) =>
        TaskOptionsButton.Flyout?.ShowAt(sender as FrameworkElement ?? TaskOptionsButton);

    private void OnWorkerCountChanged(NumberBox sender, NumberBoxValueChangedEventArgs args) => UpdateWorkerCountLabel();

    private void UpdateWorkerCountLabel()
    {
        var count = WorkerCount;
        WorkerCountLabel.Text = $"{count} Worker{(count == 1 ? string.Empty : "s")}";
    }

    private void OnBaseRefChanged(object sender, TextChangedEventArgs args) => BaseRefLabel.Text = BaseReference;

    private void OnPromptKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key != VirtualKey.Enter)
        {
            return;
        }
        var controlDown = Microsoft.UI.Input.InputKeyboardSource
            .GetKeyStateForCurrentThread(VirtualKey.Control)
            .HasFlag(Windows.UI.Core.CoreVirtualKeyStates.Down);
        if (!controlDown)
        {
            return;
        }
        args.Handled = true;
        PlanRequested?.Invoke(this, EventArgs.Empty);
    }

    private void OnDragOver(object sender, DragEventArgs args)
    {
        if (_isPlanning || !args.DataView.Contains(StandardDataFormats.StorageItems))
        {
            return;
        }
        args.AcceptedOperation = DataPackageOperation.Copy;
        args.DragUIOverride.Caption = "Attach files";
        args.DragUIOverride.IsCaptionVisible = true;
        args.Handled = true;
    }

    private async void OnDrop(object sender, DragEventArgs args)
    {
        if (_isPlanning || !args.DataView.Contains(StandardDataFormats.StorageItems))
        {
            return;
        }
        try
        {
            var items = await args.DataView.GetStorageItemsAsync();
            FilesDropped?.Invoke(this, new ComposerFilesEventArgs(items.OfType<StorageFile>().ToList()));
        }
        catch (Exception exception)
        {
            DropFailed?.Invoke(this, new ComposerErrorEventArgs(exception));
        }
    }

    private void OnRemoveAttachment(object sender, RoutedEventArgs args)
    {
        if (!_isPlanning && sender is FrameworkElement { Tag: string attachmentId })
        {
            AttachmentRemovalRequested?.Invoke(this, new ComposerAttachmentEventArgs(attachmentId));
        }
    }
}
