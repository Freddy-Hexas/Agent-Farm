using AgentFarm_Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace AgentFarm_Desktop.Views;

public sealed partial class SettingsSurface : UserControl
{
    public static readonly DependencyProperty ViewModelProperty = DependencyProperty.Register(
        nameof(ViewModel), typeof(MainPageViewModel), typeof(SettingsSurface), new PropertyMetadata(null));

    public SettingsSurface() => InitializeComponent();

    public MainPageViewModel ViewModel
    {
        get => (MainPageViewModel)GetValue(ViewModelProperty);
        set => SetValue(ViewModelProperty, value);
    }

    public event EventHandler? SaveRequested;
    public event EventHandler<SelectionChangedEventArgs>? SupervisorProviderChanged;
    public event EventHandler<SelectionChangedEventArgs>? SupervisorModelChanged;
    public event EventHandler? AddWorkerProfileRequested;
    public event EventHandler<SelectionChangedEventArgs>? WorkerProfileChanged;
    public event EventHandler<SelectionChangedEventArgs>? WorkerProviderChanged;
    public event EventHandler<SelectionChangedEventArgs>? WorkerModelChanged;
    public event EventHandler? RemoveWorkerProfileRequested;
    public event EventHandler? DiagnosticsExportRequested;
    public event EventHandler<SelectionChangedEventArgs>? ReleaseChannelChanged;
    public event EventHandler? UpdateCheckRequested;
    public event EventHandler? UpdateInstallRequested;

    public ProviderSurface Providers => ProviderSettings;
    public ComboBox SupervisorProvider => SupervisorProviderCombo;
    public ComboBox SupervisorModel => SupervisorModelCombo;
    public TextBox SupervisorCustomModel => SupervisorCustomModelBox;
    public ComboBox SupervisorThinking => SupervisorThinkingCombo;
    public ComboBox SupervisorEffort => SupervisorEffortCombo;
    public NumberBox FarmBudget => FarmBudgetBox;
    public NumberBox MonthlyBudget => MonthlyBudgetBox;
    public ComboBox BudgetPolicy => BudgetPolicyCombo;
    public NumberBox BudgetWarningRatio => BudgetWarningRatioBox;
    public ComboBox DefaultWorkerProfile => DefaultWorkerProfileCombo;
    public ListView WorkerProfiles => WorkerProfileList;
    public StackPanel WorkerProfileEditor => WorkerProfileEditorPanel;
    public ComboBox WorkerProvider => WorkerProviderCombo;
    public ComboBox WorkerModel => WorkerModelCombo;
    public TextBox WorkerCustomModel => WorkerCustomModelBox;
    public ComboBox WorkerThinking => WorkerThinkingCombo;
    public ComboBox WorkerEffort => WorkerEffortCombo;
    public ComboBox WorkerCapability => WorkerCapabilityCombo;
    public ComboBox ReleaseChannel => ReleaseChannelCombo;
    public Button InstallUpdate => InstallUpdateButton;

    public void FocusPrimary()
    {
        if (ViewModel.Settings.IsProvidersVisible)
        {
            ProviderSettings.FocusPrimary();
        }
        else
        {
            SupervisorProviderCombo.Focus(FocusState.Programmatic);
        }
    }

    public void ShowAgentRoutes()
    {
        ViewModel.Settings.ShowAgentRoutesCommand.Execute(null);
    }

    public void ShowProviders()
    {
        ViewModel.Settings.ShowProvidersCommand.Execute(null);
    }

    public static Visibility BoolToVisibility(bool value) =>
        value ? Visibility.Visible : Visibility.Collapsed;

    private void OnSave(object sender, RoutedEventArgs args) => SaveRequested?.Invoke(this, EventArgs.Empty);
    private void OnSupervisorProviderSelectionChanged(object sender, SelectionChangedEventArgs args) => SupervisorProviderChanged?.Invoke(this, args);
    private void OnSupervisorModelSelectionChanged(object sender, SelectionChangedEventArgs args) => SupervisorModelChanged?.Invoke(this, args);
    private void OnAddWorkerProfile(object sender, RoutedEventArgs args) => AddWorkerProfileRequested?.Invoke(this, EventArgs.Empty);
    private void OnWorkerProfileSelectionChanged(object sender, SelectionChangedEventArgs args) => WorkerProfileChanged?.Invoke(this, args);
    private void OnWorkerProviderSelectionChanged(object sender, SelectionChangedEventArgs args) => WorkerProviderChanged?.Invoke(this, args);
    private void OnWorkerModelSelectionChanged(object sender, SelectionChangedEventArgs args) => WorkerModelChanged?.Invoke(this, args);
    private void OnRemoveWorkerProfile(object sender, RoutedEventArgs args) => RemoveWorkerProfileRequested?.Invoke(this, EventArgs.Empty);
    private void OnExportDiagnostics(object sender, RoutedEventArgs args) => DiagnosticsExportRequested?.Invoke(this, EventArgs.Empty);
    private void OnReleaseChannelChanged(object sender, SelectionChangedEventArgs args) => ReleaseChannelChanged?.Invoke(this, args);
    private void OnCheckUpdates(object sender, RoutedEventArgs args) => UpdateCheckRequested?.Invoke(this, EventArgs.Empty);
    private void OnInstallUpdate(object sender, RoutedEventArgs args) => UpdateInstallRequested?.Invoke(this, EventArgs.Empty);
}
