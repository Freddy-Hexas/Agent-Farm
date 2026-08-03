using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;
using System.IO.Compression;
using System.Text.Json;
using System.Text.RegularExpressions;

// To learn more about WinUI, the WinUI project structure,
// and more about our project templates, see: http://aka.ms/winui-project-info.

namespace AgentFarm_Desktop;

/// <summary>
/// Provides application-specific behavior to supplement the default Application class.
/// </summary>
public partial class App : Application
{
    private static readonly object DiagnosticLock = new();
    /// <summary>
    /// The main application window. Use <c>App.Window</c> from any class that needs
    /// the window reference (for dialogs, pickers, interop, etc.).
    /// </summary>
    public static Window Window { get; private set; } = null!;

    /// <summary>
    /// The UI thread dispatcher. Use <c>App.DispatcherQueue</c> to marshal calls
    /// to the UI thread. Fully qualified to avoid CS0104 ambiguity with
    /// <see cref="Windows.System.DispatcherQueue"/>.
    /// </summary>
    public static Microsoft.UI.Dispatching.DispatcherQueue DispatcherQueue { get; private set; } = null!;

    /// <summary>
    /// The native window handle (HWND). Use for file pickers,
    /// <c>DataTransferManager</c>, and any WinRT interop that requires
    /// <c>InitializeWithWindow</c>.
    /// </summary>
    public static nint WindowHandle =>
        WinRT.Interop.WindowNative.GetWindowHandle(Window);

    /// <summary>
    /// Initializes the singleton application object.
    /// </summary>
    public App()
    {
        WriteMarker("App constructor entered");
        InitializeComponent();
        WriteMarker("App XAML initialized");
        UnhandledException += (_, args) =>
            WriteDiagnostic("Unhandled XAML exception", args.Exception);
        TaskScheduler.UnobservedTaskException += (_, args) =>
            WriteDiagnostic("Unobserved task exception", args.Exception);
    }

    /// <summary>
    /// Invoked when the application is launched.
    /// </summary>
    /// <param name="args">Details about the launch request and process.</param>
    protected override void OnLaunched(Microsoft.UI.Xaml.LaunchActivatedEventArgs args)
    {
        WriteMarker("Application launch entered");
        try
        {
            Window = new MainWindow();
            WriteMarker("Main window constructed");
            DispatcherQueue = Microsoft.UI.Dispatching.DispatcherQueue.GetForCurrentThread();
            Window.Activate();
        }
        catch (Exception exception)
        {
            WriteDiagnostic("Window launch failed", exception);
            throw;
        }
    }

    private static void WriteDiagnostic(string context, Exception exception)
    {
        try
        {
            WriteEvent(
                "error",
                context,
                exception.GetType().FullName,
                Sanitize(exception.ToString()));
        }
        catch
        {
            // Diagnostics must never replace the original failure.
        }
    }

    internal static void WriteMarker(string message)
    {
        try
        {
            WriteEvent("information", message);
        }
        catch
        {
            // Startup tracing is best effort only.
        }
    }

    internal static void AddDesktopDiagnosticsToBundle(string bundlePath)
    {
        if (!File.Exists(bundlePath) || !File.Exists(DesktopLogPath))
        {
            return;
        }

        lock (DiagnosticLock)
        {
            using var archive = ZipFile.Open(bundlePath, ZipArchiveMode.Update);
            const string entryName = "logs/desktop-events.jsonl";
            archive.GetEntry(entryName)?.Delete();
            archive.CreateEntryFromFile(DesktopLogPath, entryName, CompressionLevel.SmallestSize);
        }
    }

    private static string DesktopLogPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Agent Farm",
        "Logs",
        "desktop-events.jsonl");

    private static void WriteEvent(
        string level,
        string message,
        string? exceptionType = null,
        string? exception = null)
    {
        var path = DesktopLogPath;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var entry = new
        {
            timestamp = DateTimeOffset.UtcNow,
            level,
            component = "desktop",
            event_name = "desktop.lifecycle",
            message = Sanitize(message),
            exception_type = exceptionType,
            exception,
        };
        var line = JsonSerializer.Serialize(entry) + Environment.NewLine;
        lock (DiagnosticLock)
        {
            File.AppendAllText(path, line, new System.Text.UTF8Encoding(false));
        }
    }

    private static string Sanitize(string value)
    {
        var redacted = Regex.Replace(
            value,
            @"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\""']+",
            "$1***");
        redacted = Regex.Replace(
            redacted,
            @"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+",
            "$1***");
        return Regex.Replace(redacted, @"\bsk-[A-Za-z0-9_-]{12,}\b", "***");
    }
}
