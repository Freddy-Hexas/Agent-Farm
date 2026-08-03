using System.Text.RegularExpressions;

namespace AgentFarm.Core;

public static partial class UpdatePolicy
{
    public static Version? ParseVersion(string value)
    {
        var match = VersionPattern().Match(value ?? string.Empty);
        if (!match.Success)
        {
            return null;
        }

        var captured = match.Groups[1].Value;
        var normalized = captured.Count(character => character == '.') == 2
            ? captured + ".0"
            : captured;
        return Version.TryParse(normalized, out var version) ? version : null;
    }

    public static bool IsApprovedReleaseUri(string value)
    {
        return Uri.TryCreate(value, UriKind.Absolute, out var uri) &&
               uri.Scheme == Uri.UriSchemeHttps &&
               (uri.Host.Equals("github.com", StringComparison.OrdinalIgnoreCase) ||
                uri.Host.EndsWith(".githubusercontent.com", StringComparison.OrdinalIgnoreCase));
    }

    public static TimeSpan AutomaticCheckCadence(string channel)
    {
        return channel.Equals("preview", StringComparison.OrdinalIgnoreCase)
            ? TimeSpan.FromHours(4)
            : TimeSpan.FromHours(24);
    }

    [GeneratedRegex(@"(?<!\d)(\d+\.\d+\.\d+(?:\.\d+)?)(?!\d)")]
    private static partial Regex VersionPattern();
}
