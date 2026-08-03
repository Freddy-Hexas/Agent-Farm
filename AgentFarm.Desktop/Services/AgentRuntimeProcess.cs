using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AgentFarm_Desktop.Services;

internal sealed class AgentRuntimeProcess : IDisposable
{
    private const int RuntimeProtocolVersion = 1;
    private Process? _startupProcess;
    private bool _disposed;

    public string? RepositoryRoot { get; private set; }
    public Uri? RuntimeUri { get; private set; }

    public async Task<Uri> StartAsync(
        CancellationToken cancellationToken,
        string? requestedRepository = null)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (_startupProcess is not null || RuntimeUri is not null)
        {
            throw new InvalidOperationException("The Agent Farm runtime is already connected.");
        }

        RepositoryRoot = FindRepositoryRoot(requestedRepository)
            ?? throw new DirectoryNotFoundException(
                "Agent Farm could not locate a Git repository. Choose the project folder you want the agents to work in.");

        var existing = await TryConnectAsync(RepositoryRoot, cancellationToken);
        if (existing is not null)
        {
            RuntimeUri = existing;
            return existing;
        }

        var staleDescriptor = ReadRuntimeDescriptor(RepositoryRoot);
        if (staleDescriptor is not null)
        {
            await RequestRuntimeStopAsync(staleDescriptor.Value.Uri, cancellationToken);
        }

        var startInfo = CreateStartInfo(RepositoryRoot);
        _startupProcess = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        if (!_startupProcess.Start())
        {
            throw new InvalidOperationException("The Agent Farm daemon process could not be started.");
        }

        try
        {
            var runtimeUri = await WaitForRuntimeAsync(RepositoryRoot, cancellationToken);
            RuntimeUri = runtimeUri;
            DetachStartupProcess();
            return runtimeUri;
        }
        catch (OperationCanceledException)
        {
            StopStartupProcess();
            throw;
        }
        catch (Exception exception)
        {
            StopStartupProcess();
            var details = ReadRuntimeLogTail(RepositoryRoot);
            if (details.Length > 0)
            {
                throw new InvalidOperationException(
                    $"The Agent Farm daemon did not become ready.\n\n{details}",
                    exception);
            }
            throw;
        }
    }

    public void Stop()
    {
        RuntimeUri = null;
        StopStartupProcess();
    }

    private void StopStartupProcess()
    {
        var process = Interlocked.Exchange(ref _startupProcess, null);
        if (process is null)
        {
            return;
        }

        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(3000);
            }
        }
        catch (InvalidOperationException)
        {
            // The process completed between the state check and shutdown.
        }
        finally
        {
            process.Dispose();
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        // The repository daemon deliberately outlives the desktop window. Stop()
        // only terminates a daemon that is still in its startup phase.
        Stop();
    }

    private void DetachStartupProcess()
    {
        var process = Interlocked.Exchange(ref _startupProcess, null);
        process?.Dispose();
    }

    private static ProcessStartInfo CreateStartInfo(string repositoryRoot)
    {
        var startInfo = new ProcessStartInfo
        {
            WorkingDirectory = repositoryRoot,
            RedirectStandardOutput = false,
            RedirectStandardError = false,
            UseShellExecute = false,
            CreateNoWindow = true,
        };

#if DEBUG
        // Debug always runs the repository source. Python, HTML, CSS, and
        // JavaScript changes therefore take effect after a normal app restart
        // without rebuilding the PyInstaller backend.
        startInfo.FileName = FindPythonExecutable();
        startInfo.ArgumentList.Add("-m");
        startInfo.ArgumentList.Add("agent_farm.desktop_server");
        var sourceRoot = FindSourceRoot()
            ?? throw new DirectoryNotFoundException(
                "Debug could not locate the Agent Farm Python source. Set AGENT_FARM_SOURCE_ROOT to the Agent Farm source repository.");
        var existingPythonPath = Environment.GetEnvironmentVariable("PYTHONPATH");
        startInfo.Environment["PYTHONPATH"] = string.IsNullOrWhiteSpace(existingPythonPath)
            ? sourceRoot
            : sourceRoot + Path.PathSeparator + existingPythonPath;
#else
        // Release is deliberately hermetic: it must use the frozen backend
        // shipped beside the WinUI executable and never depend on system Python.
        var packagedBackend = Path.Combine(
            AppContext.BaseDirectory,
            "Backend",
            "AgentFarmBackend.exe");
        if (!File.Exists(packagedBackend))
        {
            throw new FileNotFoundException(
                "The packaged Agent Farm backend is missing from this Release build.",
                packagedBackend);
        }
        startInfo.FileName = packagedBackend;
#endif

        startInfo.ArgumentList.Add("--repo");
        startInfo.ArgumentList.Add(repositoryRoot);
        startInfo.ArgumentList.Add("--daemon");
        startInfo.Environment["PYTHONUNBUFFERED"] = "1";
        startInfo.Environment["AGENT_FARM_RUNTIME_FINGERPRINT"] = ExpectedRuntimeFingerprint();
        return startInfo;
    }

    private static async Task<Uri?> TryConnectAsync(
        string repositoryRoot,
        CancellationToken cancellationToken)
    {
        var descriptor = ReadRuntimeDescriptor(repositoryRoot);
        if (descriptor is null)
        {
            return null;
        }
        return await IsHealthyAsync(descriptor.Value.Uri, repositoryRoot, cancellationToken)
            ? descriptor.Value.Uri
            : null;
    }

    private async Task<Uri> WaitForRuntimeAsync(
        string repositoryRoot,
        CancellationToken cancellationToken)
    {
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var connected = await TryConnectAsync(repositoryRoot, cancellationToken);
            if (connected is not null)
            {
                return connected;
            }

            var process = _startupProcess;
            if (process is not null && process.HasExited && process.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    $"The Agent Farm daemon exited during startup with code {process.ExitCode}.");
            }
            await Task.Delay(TimeSpan.FromMilliseconds(100), cancellationToken);
        }
    }

    private static (Uri Uri, int Pid)? ReadRuntimeDescriptor(string repositoryRoot)
    {
        var path = RuntimeDescriptorPath(repositoryRoot);
        try
        {
            using var payload = JsonDocument.Parse(File.ReadAllText(path));
            var root = payload.RootElement;
            if (root.GetProperty("schema_version").GetInt32() != 1 ||
                root.GetProperty("protocol_version").GetInt32() != RuntimeProtocolVersion)
            {
                return null;
            }
            var repository = root.GetProperty("repository").GetString();
            var url = root.GetProperty("url").GetString();
            var pid = root.GetProperty("pid").GetInt32();
            if (repository is null ||
                !Path.GetFullPath(repository).Equals(
                    Path.GetFullPath(repositoryRoot),
                    StringComparison.OrdinalIgnoreCase) ||
                url is null ||
                !Uri.TryCreate(url, UriKind.Absolute, out var uri))
            {
                return null;
            }
            return (uri, pid);
        }
        catch (FileNotFoundException)
        {
            return null;
        }
        catch (DirectoryNotFoundException)
        {
            return null;
        }
        catch (IOException)
        {
            return null;
        }
        catch (JsonException)
        {
            return null;
        }
        catch (KeyNotFoundException)
        {
            return null;
        }
        catch (InvalidOperationException)
        {
            return null;
        }
    }

    private static async Task<bool> IsHealthyAsync(
        Uri runtimeUri,
        string repositoryRoot,
        CancellationToken cancellationToken)
    {
        var authority = runtimeUri.GetLeftPart(UriPartial.Authority);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(1));
        using var client = new HttpClient { BaseAddress = new Uri(authority + "/") };
        try
        {
            using var response = await client.GetAsync("api/health", timeout.Token);
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }
            using var payload = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(timeout.Token),
                cancellationToken: timeout.Token);
            var root = payload.RootElement;
            return root.GetProperty("status").GetString() == "ok" &&
                   root.GetProperty("protocol_version").GetInt32() == RuntimeProtocolVersion &&
                   root.TryGetProperty("runtime_fingerprint", out var fingerprint) &&
                   fingerprint.GetString() == ExpectedRuntimeFingerprint() &&
                   Path.GetFullPath(root.GetProperty("repository").GetString() ?? string.Empty).Equals(
                       Path.GetFullPath(repositoryRoot),
                       StringComparison.OrdinalIgnoreCase);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return false;
        }
        catch (HttpRequestException)
        {
            return false;
        }
        catch (IOException)
        {
            return false;
        }
        catch (JsonException)
        {
            return false;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
        catch (KeyNotFoundException)
        {
            return false;
        }
    }

    private static async Task RequestRuntimeStopAsync(
        Uri runtimeUri,
        CancellationToken cancellationToken)
    {
        var authority = runtimeUri.GetLeftPart(UriPartial.Authority);
        using var client = new HttpClient { BaseAddress = new Uri(authority + "/") };
        using var content = new ByteArrayContent("{}"u8.ToArray());
        content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/json");
        content.Headers.ContentLength = 2;
        try
        {
            using var response = await client.PostAsync("api/runtime/stop", content, cancellationToken);
            if (response.IsSuccessStatusCode)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(250), cancellationToken);
            }
        }
        catch (HttpRequestException)
        {
            // A stale descriptor is harmless when its former process is already gone.
        }
    }

    private static string ExpectedRuntimeFingerprint()
    {
#if DEBUG
        var sourceRoot = FindSourceRoot();
        if (sourceRoot is not null)
        {
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            var packageRoot = Path.Combine(sourceRoot, "agent_farm");
            foreach (var path in Directory.EnumerateFiles(packageRoot, "*.py", SearchOption.AllDirectories)
                         .OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
            {
                var relative = Path.GetRelativePath(sourceRoot, path).Replace('\\', '/');
                hash.AppendData(Encoding.UTF8.GetBytes(relative + "\n"));
                hash.AppendData(File.ReadAllBytes(path));
            }
            return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
        }
#endif
        return typeof(AgentRuntimeProcess).Assembly.GetName().Version?.ToString() ?? "unknown";
    }

    private static string RuntimeDescriptorPath(string repositoryRoot) =>
        Path.Combine(repositoryRoot, ".agent-farm", "runtime.json");

    private static string ReadRuntimeLogTail(string repositoryRoot)
    {
        var path = Path.Combine(repositoryRoot, ".agent-farm", "logs", "runtime.log");
        try
        {
            var text = File.ReadAllText(path);
            return text.Length <= 8000 ? text.Trim() : text[^8000..].Trim();
        }
        catch (IOException)
        {
            return string.Empty;
        }
        catch (UnauthorizedAccessException)
        {
            return string.Empty;
        }
    }

    private static string FindPythonExecutable()
    {
        var configured = Environment.GetEnvironmentVariable("AGENT_FARM_PYTHON");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            if (!File.Exists(configured))
            {
                throw new FileNotFoundException("AGENT_FARM_PYTHON does not point to a Python executable.", configured);
            }
            return configured;
        }

        var condaPrefix = Environment.GetEnvironmentVariable("CONDA_PREFIX");
        if (!string.IsNullOrWhiteSpace(condaPrefix))
        {
            var condaPython = Path.Combine(condaPrefix, "python.exe");
            if (File.Exists(condaPython))
            {
                return condaPython;
            }
        }

        var userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        foreach (var distribution in new[] { "miniconda3", "anaconda3" })
        {
            var distributionPython = Path.Combine(userProfile, distribution, "python.exe");
            if (File.Exists(distributionPython))
            {
                return distributionPython;
            }
        }

        var localPrograms = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs",
            "Python");
        if (Directory.Exists(localPrograms))
        {
            var installedPython = Directory
                .EnumerateFiles(localPrograms, "python.exe", SearchOption.AllDirectories)
                .OrderByDescending(path => path, StringComparer.OrdinalIgnoreCase)
                .FirstOrDefault();
            if (installedPython is not null)
            {
                return installedPython;
            }
        }

        var pathValue = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        foreach (var entry in pathValue.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            var pathPython = Path.Combine(entry.Trim(), "python.exe");
            if (File.Exists(pathPython) &&
                !pathPython.Contains("Microsoft\\WindowsApps", StringComparison.OrdinalIgnoreCase))
            {
                return pathPython;
            }
        }

        return "python.exe";
    }

    public static void RememberRepository(string repositoryRoot)
    {
        var normalized = FindRepositoryFrom(repositoryRoot)
            ?? throw new DirectoryNotFoundException("Choose a folder inside a Git repository.");
        var directory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Agent Farm");
        Directory.CreateDirectory(directory);
        File.WriteAllText(Path.Combine(directory, "last-repository.txt"), normalized);
    }

    private static string? FindRepositoryRoot(string? requestedRepository)
    {
        var requested = FindRepositoryFrom(requestedRepository);
        if (requested is not null)
        {
            return requested;
        }

        var configured = Environment.GetEnvironmentVariable("AGENT_FARM_REPO");
        var configuredRoot = FindRepositoryFrom(configured);
        if (configuredRoot is not null)
        {
            return configuredRoot;
        }

        var rememberedPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Agent Farm",
            "last-repository.txt");
        if (File.Exists(rememberedPath))
        {
            var rememberedRoot = FindRepositoryFrom(File.ReadAllText(rememberedPath).Trim());
            if (rememberedRoot is not null)
            {
                return rememberedRoot;
            }
        }

        foreach (var candidate in new[] { Environment.CurrentDirectory, AppContext.BaseDirectory })
        {
            var discoveredRoot = FindRepositoryFrom(candidate);
            if (discoveredRoot is not null)
            {
                return discoveredRoot;
            }
        }

        return null;
    }

    private static string? FindRepositoryFrom(string? candidate)
    {
        if (string.IsNullOrWhiteSpace(candidate) || !Directory.Exists(candidate))
        {
            return null;
        }

        var directory = new DirectoryInfo(Path.GetFullPath(candidate));
        while (directory is not null)
        {
            if (IsRepositoryRoot(directory.FullName))
            {
                return directory.FullName;
            }
            directory = directory.Parent;
        }

        return null;
    }

    private static bool IsRepositoryRoot(string path)
    {
        var gitMetadata = Path.Combine(path, ".git");
        return Directory.Exists(gitMetadata) || File.Exists(gitMetadata);
    }

    private static string? FindSourceRoot()
    {
        var configured = Environment.GetEnvironmentVariable("AGENT_FARM_SOURCE_ROOT");
        foreach (var candidate in new[] { configured, AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            if (string.IsNullOrWhiteSpace(candidate) || !Directory.Exists(candidate))
            {
                continue;
            }
            var directory = new DirectoryInfo(Path.GetFullPath(candidate));
            while (directory is not null)
            {
                if (Directory.Exists(Path.Combine(directory.FullName, "agent_farm")) &&
                    File.Exists(Path.Combine(directory.FullName, "pyproject.toml")))
                {
                    return directory.FullName;
                }
                directory = directory.Parent;
            }
        }
        return null;
    }

}
