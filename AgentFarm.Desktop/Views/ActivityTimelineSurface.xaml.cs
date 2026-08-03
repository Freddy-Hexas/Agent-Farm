using AgentFarm_Desktop.Models;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Collections.ObjectModel;
using System.Collections.Specialized;

namespace AgentFarm_Desktop.Views;

public sealed partial class ActivityTimelineSurface : UserControl
{
    public static readonly DependencyProperty ItemsSourceProperty = DependencyProperty.Register(
        nameof(ItemsSource),
        typeof(ObservableCollection<TimelineEntry>),
        typeof(ActivityTimelineSurface),
        new PropertyMetadata(null, OnItemsSourceChanged));

    private ObservableCollection<TimelineEntry>? _observedItems;

    public ActivityTimelineSurface()
    {
        InitializeComponent();
        UpdateEmptyState();
    }

    public ObservableCollection<TimelineEntry>? ItemsSource
    {
        get => (ObservableCollection<TimelineEntry>?)GetValue(ItemsSourceProperty);
        set => SetValue(ItemsSourceProperty, value);
    }

    private static void OnItemsSourceChanged(DependencyObject dependencyObject, DependencyPropertyChangedEventArgs args)
    {
        if (dependencyObject is not ActivityTimelineSurface surface)
        {
            return;
        }

        surface.Observe(args.NewValue as ObservableCollection<TimelineEntry>);
    }

    private void Observe(ObservableCollection<TimelineEntry>? items)
    {
        if (_observedItems is not null)
        {
            _observedItems.CollectionChanged -= OnCollectionChanged;
        }

        _observedItems = items;
        ActivityList.ItemsSource = items;
        if (_observedItems is not null)
        {
            _observedItems.CollectionChanged += OnCollectionChanged;
        }

        UpdateEmptyState();
    }

    private void OnCollectionChanged(object? sender, NotifyCollectionChangedEventArgs args) => UpdateEmptyState();

    private void UpdateEmptyState()
    {
        var isEmpty = _observedItems is null || _observedItems.Count == 0;
        EmptyState.Visibility = isEmpty ? Visibility.Visible : Visibility.Collapsed;
        ActivityList.Visibility = isEmpty ? Visibility.Collapsed : Visibility.Visible;
    }
}
