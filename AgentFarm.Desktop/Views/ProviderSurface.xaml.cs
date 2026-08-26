using AgentFarm_Desktop.Models;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Collections.ObjectModel;

namespace AgentFarm_Desktop.Views;

public sealed partial class ProviderSurface : UserControl
{
    public static readonly DependencyProperty TemplatesProperty = DependencyProperty.Register(nameof(Templates), typeof(ObservableCollection<ProviderTemplate>), typeof(ProviderSurface), new PropertyMetadata(null, OnTemplatesChanged));
    public static readonly DependencyProperty ProvidersProperty = DependencyProperty.Register(nameof(Providers), typeof(ObservableCollection<ProviderEditor>), typeof(ProviderSurface), new PropertyMetadata(null, OnProvidersChanged));

    public ProviderSurface() => InitializeComponent();

    public void FocusPrimary() => TemplateCombo.Focus(FocusState.Programmatic);
    public event EventHandler? AddRequested;
    public event EventHandler? SelectionChanged;
    public event EventHandler? WireApiChanged;
    public event EventHandler? ApiKeyChanged;
    public event EventHandler? RefreshRequested;
    public event EventHandler? RemoveRequested;

    public ObservableCollection<ProviderTemplate>? Templates { get => (ObservableCollection<ProviderTemplate>?)GetValue(TemplatesProperty); set => SetValue(TemplatesProperty, value); }
    public ObservableCollection<ProviderEditor>? Providers { get => (ObservableCollection<ProviderEditor>?)GetValue(ProvidersProperty); set => SetValue(ProvidersProperty, value); }
    public ProviderTemplate? SelectedTemplate { get => TemplateCombo.SelectedItem as ProviderTemplate; set => TemplateCombo.SelectedItem = value; }
    public ProviderEditor? SelectedProvider { get => ProviderList.SelectedItem as ProviderEditor; set => ProviderList.SelectedItem = value; }
    public int SelectedProviderIndex { get => ProviderList.SelectedIndex; set => ProviderList.SelectedIndex = value; }
    public object? EditorDataContext { get => EditorPanel.DataContext; set => EditorPanel.DataContext = value; }
    public string ProviderId { get => ProviderIdBox.Text; set => ProviderIdBox.Text = value; }
    public string WireApi { get => WireApiCombo.SelectedItem as string ?? "chat"; set => WireApiCombo.SelectedItem = value; }
    public string ApiKey { get => ApiKeyBox.Password; set => ApiKeyBox.Password = value; }

    private static void OnTemplatesChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ProviderSurface)sender).TemplateCombo.ItemsSource = args.NewValue;
    private static void OnProvidersChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) => ((ProviderSurface)sender).ProviderList.ItemsSource = args.NewValue;
    private void OnAdd(object sender, RoutedEventArgs args) => AddRequested?.Invoke(this, EventArgs.Empty);
    private void OnSelectionChanged(object sender, SelectionChangedEventArgs args) => SelectionChanged?.Invoke(this, EventArgs.Empty);
    private void OnWireApiChanged(object sender, SelectionChangedEventArgs args) => WireApiChanged?.Invoke(this, EventArgs.Empty);
    private void OnApiKeyChanged(object sender, RoutedEventArgs args) => ApiKeyChanged?.Invoke(this, EventArgs.Empty);
    private void OnRefresh(object sender, RoutedEventArgs args) => RefreshRequested?.Invoke(this, EventArgs.Empty);
    private void OnRemove(object sender, RoutedEventArgs args) => RemoveRequested?.Invoke(this, EventArgs.Empty);
}
