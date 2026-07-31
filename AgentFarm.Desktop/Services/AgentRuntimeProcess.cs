using System.Diagnostics;
using System.Text.Json;

namespace AgentFarm_Desktop.Services;

internal sealed class AgentRuntimeProcess : IDisposable
{
    private const string ReadyPrefix = "AGENT_FARM_READY ";
    private Process? _process;
    private Task<string>? _stderrTask;
    private bool _disposed;

    public string? RepositoryRoot { get; private set; }

    public async Task<Uri> StartAsync(
        CancellationToken cancellationToken,
        string? requestedRepository = null)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (_process is not null)
        {
            throw new InvalidOperationException("The Agent Farm runtime is already running.");
        }

        RepositoryRoot = FindRepositoryRoot(requestedRepository)
            ?? throw new DirectoryNotFoundException(
                "Agent Farm could not locate a Git repository. Choose the project folder you want the agents to work in.");

        var startInfo = CreateStartInfo(RepositoryRoot);
        _process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        if (!_process.Start())
        {
            throw new InvalidOperationException("The Agent Farm runtime process could not be started.");
        }

        _stderrTask = _process.StandardError.ReadToEndAsync(cancellationToken);
        try
        {
            while (true)
            {
                var line = await _process.StandardOutput.ReadLineAsync(cancellationToken);
                if (line is null)
                {
                    var details = await ReadErrorOutputAsync();
                    throw new InvalidOperationException(
                        $"The Agent Farm runtime stopped before it was ready.{details}");
                }

                if (!line.StartsWith(ReadyPrefix, StringComparison.Ordinal))
                {
                    continue;
                }

                using var payload = JsonDocument.Parse(line[ReadyPrefix.Length..]);
                var url = payload.RootElement.TryGetProperty("url", out var urlElement)
                    ? urlElement.GetString()
                    : null;
                if (url is null || !Uri.TryCreate(url, UriKind.Absolute, out var runtimeUri))
                {
                    throw new InvalidOperationException("The Agent Farm runtime returned an invalid address.");
                }

                return runtimeUri;
            }
        }
        catch
        {
            Stop();
            throw;
        }
    }

    public void Stop()
    {
        var process = Interlocked.Exchange(ref _process, null);
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
        Stop();
    }

    private static ProcessStartInfo CreateStartInfo(string repositoryRoot)
    {
        var startInfo = new ProcessStartInfo
        {
            WorkingDirectory = repositoryRoot,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
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
        startInfo.Environment["PYTHONUNBUFFERED"] = "1";
        return startInfo;
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

    private async Task<string> ReadErrorOutputAsync()
    {
        if (_stderrTask is null)
        {
            return string.Empty;
        }

        var output = (await _stderrTask).Trim();
        return output.Length == 0 ? string.Empty : $"\n\n{output}";
    }

}
