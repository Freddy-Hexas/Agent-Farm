using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.UI;

namespace AgentFarm_Desktop;

public sealed partial class MainWindow : Window
{
    private bool _startupStateApplied;

    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.SetIcon("Assets/AppIcon.ico");
        ConfigureTitleBar();

        Activated += OnFirstActivated;
        AppWindow.Closing += OnWindowClosing;
        RootFrame.Navigate(typeof(MainPage));
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
            presenter.Maximize();
        }
    }

    private void OnWindowClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (RootFrame.Content is MainPage page)
        {
            page.StopRuntime();
        }
    }
}
