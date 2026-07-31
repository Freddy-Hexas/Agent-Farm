using AgentFarm_Desktop.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace AgentFarm_Desktop;

public sealed partial class MainPage : Page
{
    private readonly AgentRuntimeProcess _runtime = new();
    private readonly CancellationTokenSource _startupCancellation = new();
    private bool _started;

    public MainPage()
    {
        InitializeComponent();
    }

    public void StopRuntime()
    {
        _startupCancellation.Cancel();
        _runtime.Dispose();
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_started)
        {
            return;
        }

        _started = true;
        await StartRuntimeAsync();
    }

    private async Task StartRuntimeAsync(string? repositoryRoot = null)
    {
        StartupError.IsOpen = false;
        LoadingOverlay.Visibility = Visibility.Visible;
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_startupCancellation.Token);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));

            var runtimeUri = await _runtime.StartAsync(timeout.Token, repositoryRoot);
            await AgentWebView.EnsureCoreWebView2Async();

            var settings = AgentWebView.CoreWebView2.Settings;
            settings.AreDevToolsEnabled =
                string.Equals(Environment.GetEnvironmentVariable("AGENT_FARM_DEBUG"), "1", StringComparison.Ordinal);
            settings.IsStatusBarEnabled = false;
            settings.IsZoomControlEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
            settings.IsGeneralAutofillEnabled = false;

            AgentWebView.NavigationCompleted += OnNavigationCompleted;
            AgentWebView.CoreWebView2.ProcessFailed += OnWebViewProcessFailed;
            AgentWebView.Source = runtimeUri;
        }
        catch (OperationCanceledException) when (_startupCancellation.IsCancellationRequested)
        {
            // The window closed while startup was in progress.
        }
        catch (OperationCanceledException)
        {
            ShowStartupError("The local runtime did not become ready within 30 seconds.");
        }
        catch (Exception exception)
        {
            ShowStartupError(exception.Message);
        }
    }

    private async void OnChooseRepository(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new Windows.Storage.Pickers.FolderPicker();
            picker.FileTypeFilter.Add("*");
            WinRT.Interop.InitializeWithWindow.Initialize(picker, App.WindowHandle);
            var folder = await picker.PickSingleFolderAsync();
            if (folder is null)
            {
                return;
            }

            AgentRuntimeProcess.RememberRepository(folder.Path);
            _runtime.Stop();
            await StartRuntimeAsync(folder.Path);
        }
        catch (Exception exception)
        {
            ShowStartupError(exception.Message);
        }
    }

    private void OnNavigationCompleted(WebView2 sender, Microsoft.Web.WebView2.Core.CoreWebView2NavigationCompletedEventArgs args)
    {
        if (args.IsSuccess)
        {
            LoadingOverlay.Visibility = Visibility.Collapsed;
            return;
        }

        ShowStartupError($"The workspace UI could not be loaded ({args.WebErrorStatus}).");
    }

    private void OnWebViewProcessFailed(
        Microsoft.Web.WebView2.Core.CoreWebView2 sender,
        Microsoft.Web.WebView2.Core.CoreWebView2ProcessFailedEventArgs args)
    {
        ShowStartupError("The embedded browser process stopped unexpectedly. Restart Agent Farm.");
    }

    private void ShowStartupError(string message)
    {
        LoadingOverlay.Visibility = Visibility.Collapsed;
        StartupError.Message = message;
        StartupError.IsOpen = true;
    }
}
