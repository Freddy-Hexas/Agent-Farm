using AgentFarm_Desktop.ViewModels;
using CommunityToolkit.WinUI.Controls;
using CommunityToolkit.Mvvm.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Shapes;
using System.Collections.Specialized;

namespace AgentFarm_Desktop.Views;

public sealed partial class WorkspaceSurface : UserControl
{
    public static readonly DependencyProperty ViewModelProperty = DependencyProperty.Register(
        nameof(ViewModel), typeof(MainPageViewModel), typeof(WorkspaceSurface), new PropertyMetadata(null, OnViewModelChanged));

    public WorkspaceSurface() => InitializeComponent();

    public MainPageViewModel ViewModel
    {
        get => (MainPageViewModel)GetValue(ViewModelProperty);
        set => SetValue(ViewModelProperty, value);
    }

    public IRelayCommand ClearNotificationsCommand => ViewModel.ClearNotificationsCommand;

    public event EventHandler? RefreshRequested;
    public event EventHandler? RuntimeRecoveryRequested;
    public event SizeChangedEventHandler? ExecutionPaneSizeChanged;
    public event DoubleTappedEventHandler? ExecutionSplitterDoubleTapped;

    public ComposerSurface ComposerView => Composer;
    public ExecutionSurface ExecutionView => Execution;
    public InfoBar InfoBar => NotificationBar;
    public ColumnDefinition RightPane => ExecutionPaneColumn;
    public ColumnDefinition RightSplitterColumn => ExecutionSplitterColumn;
    public Rectangle RightDivider => ExecutionPaneDivider;
    public GridSplitter RightSplitter => ExecutionPaneSplitter;
    public Button ToggleRightPaneButton => ToggleExecutionButton;
    public FontIcon RightPaneToggleIcon => ExecutionPaneToggleIcon;

    private static void OnViewModelChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var surface = (WorkspaceSurface)sender;
        if (args.OldValue is MainPageViewModel oldViewModel)
        {
            oldViewModel.Notifications.CollectionChanged -= surface.OnNotificationsChanged;
        }
        if (args.NewValue is MainPageViewModel newViewModel)
        {
            newViewModel.Notifications.CollectionChanged += surface.OnNotificationsChanged;
            surface.NotificationQueue.ItemsSource = newViewModel.Notifications;
        }
        surface.UpdateNotificationState();
    }

    private void OnNotificationsChanged(object? sender, NotifyCollectionChangedEventArgs args) =>
        UpdateNotificationState();

    private void UpdateNotificationState()
    {
        var count = (GetValue(ViewModelProperty) as MainPageViewModel)?.Notifications.Count ?? 0;
        NotificationCountLabel.Text = count > 99 ? "99+" : count.ToString();
        NotificationCountBadge.Visibility = count > 0 ? Visibility.Visible : Visibility.Collapsed;
        NotificationQueue.Visibility = count > 0 ? Visibility.Visible : Visibility.Collapsed;
        NotificationEmptyState.Visibility = count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void OnRefresh(object sender, RoutedEventArgs args) => RefreshRequested?.Invoke(this, EventArgs.Empty);
    private void OnRetryRuntime(object sender, RoutedEventArgs args) => RuntimeRecoveryRequested?.Invoke(this, EventArgs.Empty);
    private void OnToggleExecutionPane(object sender, RoutedEventArgs args) => ViewModel.Shell.ToggleExecutionPaneCommand.Execute(null);
    private void OnExecutionSizeChanged(object sender, SizeChangedEventArgs args) => ExecutionPaneSizeChanged?.Invoke(sender, args);
    private void OnExecutionSplitterDoubleTapped(object sender, DoubleTappedRoutedEventArgs args) => ExecutionSplitterDoubleTapped?.Invoke(sender, args);
}
