using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.UI;
using Windows.Graphics;

namespace AgentFarm_Desktop;

public sealed partial class MainWindow : Window
{
    private bool _startupStateApplied;
    private bool _exitRequested;

    public MainWindow()
    {
        App.WriteMarker("MainWindow constructor entered");
        InitializeComponent();
        App.WriteMarker("MainWindow XAML initialized");

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.SetIcon("Assets/AppIcon.ico");
        ConfigureTitleBar();

        Activated += OnFirstActivated;
        AppWindow.Closing += OnWindowClosing;
        RootFrame.Navigate(typeof(MainPage));
        App.WriteMarker("MainPage navigation returned");
    }

    public void ShowWindow()
    {
        AppWindow.Show();
        Activate();
    }

    public void RequestExit()
    {
        _exitRequested = true;
        Close();
    }

    private void ConfigureTitleBar()
    {
        if (!AppWindowTitleBar.IsCustomizationSupported())
        {
            return;
        }

        var titleBar = AppWindow.TitleBar;
        titleBar.ButtonBackgroundColor = Colors.Transparent;
        titleBar.ButtonInactiveBackgroundColor = Colors.Transparent;
        titleBar.ButtonForegroundColor = Color.FromArgb(255, 45, 45, 45);
        titleBar.ButtonInactiveForegroundColor = Color.FromArgb(255, 125, 125, 125);
        titleBar.ButtonHoverBackgroundColor = Color.FromArgb(20, 0, 0, 0);
        titleBar.ButtonPressedBackgroundColor = Color.FromArgb(32, 0, 0, 0);
    }

    private void OnFirstActivated(object sender, WindowActivatedEventArgs args)
    {
        if (_startupStateApplied)
        {
            return;
        }

        _startupStateApplied = true;
        Activated -= OnFirstActivated;
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            if (IsWindowedLaunch())
            {
                AppWindow.Resize(new SizeInt32(LaunchDimension("AGENT_FARM_DESKTOP_WIDTH", 1440), LaunchDimension("AGENT_FARM_DESKTOP_HEIGHT", 900)));
            }
            else
            {
                presenter.Maximize();
            }
        }
    }

    private static bool IsWindowedLaunch() =>
        string.Equals(Environment.GetEnvironmentVariable("AGENT_FARM_WINDOWED"), "1", StringComparison.Ordinal);

    private static int LaunchDimension(string name, int fallback) =>
        int.TryParse(Environment.GetEnvironmentVariable(name), out var value)
            ? Math.Clamp(value, 1040, 10000)
            : fallback;

    private void OnWindowClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (_exitRequested)
        {
            if (RootFrame.Content is MainPage page)
            {
                page.StopRuntime();
            }
            return;
        }

        // Keep the daemon and its in-flight work alive when the user dismisses the window.
        args.Cancel = true;
        sender.Hide();
        App.WriteMarker("MainWindow hidden; background runtime remains active");
    }
}
