using AgentFarm.Core;
using AgentFarm_Desktop.Models;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using Windows.Storage;
using Windows.System;

namespace AgentFarm_Desktop.Services;

internal sealed class UpdateService : IDisposable
{
    private const string ReleasesApi = "https://api.github.com/repos/Freddy-Hexas/Agent-Farm/releases";
    private const string PackageAssetName = "AgentFarm-Native-x64.msix";
    private const string ChecksumsAssetName = "SHA256SUMS.txt";
    private readonly HttpClient _client = new()
    {
        Timeout = TimeSpan.FromSeconds(30),
    };

    public UpdateService()
    {
        _client.DefaultRequestHeaders.UserAgent.ParseAdd("AgentFarm-Desktop/0.5");
        _client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
    }

    public static Version CurrentVersion
    {
        get
        {
            var value = Windows.ApplicationModel.Package.Current.Id.Version;
            return new Version(value.Major, value.Minor, value.Build, value.Revision);
        }
    }

    public async Task<UpdateCheckResult> CheckAsync(
        string channel,
        CancellationToken cancellationToken)
    {
        var preview = channel.Equals("preview", StringComparison.OrdinalIgnoreCase);
        var endpoint = preview ? $"{ReleasesApi}?per_page=20" : $"{ReleasesApi}/latest";
        using var response = await _client.GetAsync(endpoint, cancellationToken);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);

        JsonElement release;
        if (preview)
        {
            release = document.RootElement.EnumerateArray().FirstOrDefault(candidate =>
                candidate.TryGetProperty("prerelease", out var prerelease) && prerelease.GetBoolean() &&
                candidate.TryGetProperty("draft", out var draft) && !draft.GetBoolean());
            if (release.ValueKind == JsonValueKind.Undefined)
            {
                return UpdateCheckResult.None(channel, CurrentVersion, "No Preview release is published.");
            }
        }
        else
        {
            release = document.RootElement;
        }

        var tag = release.GetProperty("tag_name").GetString() ?? string.Empty;
        var name = release.TryGetProperty("name", out var nameElement)
            ? nameElement.GetString() ?? string.Empty
            : string.Empty;
        var version = UpdatePolicy.ParseVersion(tag) ?? UpdatePolicy.ParseVersion(name);
        if (version is null)
        {
            return UpdateCheckResult.None(channel, CurrentVersion, "The release has no valid version.");
        }

        string? packageUrl = null;
        string? checksumsUrl = null;
        foreach (var asset in release.GetProperty("assets").EnumerateArray())
        {
            var assetName = asset.GetProperty("name").GetString();
            var url = asset.GetProperty("browser_download_url").GetString();
            if (assetName == PackageAssetName)
            {
                packageUrl = url;
            }
            else if (assetName == ChecksumsAssetName)
            {
                checksumsUrl = url;
            }
        }
        if (packageUrl is null || checksumsUrl is null)
        {
            return UpdateCheckResult.None(
                channel,
                CurrentVersion,
                "The release is missing its signed MSIX or checksum file.");
        }
        ValidateReleaseUri(packageUrl);
        ValidateReleaseUri(checksumsUrl);
        return new UpdateCheckResult
        {
            Channel = channel,
            CurrentVersion = CurrentVersion,
            AvailableVersion = version,
            IsAvailable = version > CurrentVersion,
            PackageUrl = packageUrl,
            ChecksumsUrl = checksumsUrl,
            Message = version > CurrentVersion
                ? $"Agent Farm {version} is available on the {channel} channel."
                : $"Agent Farm {CurrentVersion} is up to date on the {channel} channel.",
        };
    }

    public async Task<string> DownloadAndLaunchAsync(
        UpdateCheckResult update,
        CancellationToken cancellationToken)
    {
        if (!update.IsAvailable || update.PackageUrl is null || update.ChecksumsUrl is null)
        {
            throw new InvalidOperationException("No verified update is available.");
        }
        var checksums = await _client.GetStringAsync(update.ChecksumsUrl, cancellationToken);
        var expected = checksums
            .Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(line => line.Split(' ', StringSplitOptions.RemoveEmptyEntries))
            .FirstOrDefault(parts => parts.Length >= 2 && parts[^1] == PackageAssetName)?[0];
        if (expected is null || !Regex.IsMatch(expected, "^[a-fA-F0-9]{64}$"))
        {
            throw new InvalidDataException("The release checksum file is invalid.");
        }

        var outputPath = Path.Combine(
            ApplicationData.Current.TemporaryFolder.Path,
            $"AgentFarm-Native-{update.AvailableVersion}-x64.msix");
        using (var response = await _client.GetAsync(
                   update.PackageUrl,
                   HttpCompletionOption.ResponseHeadersRead,
                   cancellationToken))
        {
            response.EnsureSuccessStatusCode();
            await using var input = await response.Content.ReadAsStreamAsync(cancellationToken);
            await using var output = new FileStream(
                outputPath,
                FileMode.Create,
                FileAccess.Write,
                FileShare.None,
                81920,
                useAsync: true);
            await input.CopyToAsync(output, cancellationToken);
        }

        await using (var input = File.OpenRead(outputPath))
        {
            var actual = Convert.ToHexString(await SHA256.HashDataAsync(input, cancellationToken));
            if (!actual.Equals(expected, StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(outputPath);
                throw new InvalidDataException("The downloaded MSIX failed SHA256 verification.");
            }
        }
        var file = await StorageFile.GetFileFromPathAsync(outputPath);
        if (!await Launcher.LaunchFileAsync(file))
        {
            throw new InvalidOperationException("Windows App Installer could not open the update.");
        }
        return outputPath;
    }

    public void Dispose() => _client.Dispose();

    private static void ValidateReleaseUri(string value)
    {
        if (!UpdatePolicy.IsApprovedReleaseUri(value))
        {
            throw new InvalidDataException("The update asset is not hosted on an approved HTTPS origin.");
        }
    }
}
