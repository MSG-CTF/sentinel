using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sentinel.Monitor.Platform;

public sealed record ProbeObservation(
    string TargetId, string Name, string Role, string Track, string Kind, string? Url,
    bool Enabled, string Status, string ReasonCode, DateTimeOffset? CheckedAt,
    int? HttpStatusCode, double? LatencyMs, IReadOnlyDictionary<string, string> Components,
    string? EvidenceScope = null, RiskAssessment? Risk = null);

public sealed record MonitorEvent(
    string EventId, string TargetId, string Track, string PreviousStatus, string Status,
    string ReasonCode, DateTimeOffset CreatedAt);

public sealed record PlatformSnapshot(
    string Profile, string Mode, DateTimeOffset StartedAt, DateTimeOffset? UpdatedAt,
    int PollIntervalSeconds, int FailureThreshold, bool EventStorageHealthy,
    IReadOnlyList<ProbeObservation> Targets,
    Sentinel.Monitor.Recovery.RecoverySnapshot? Recovery = null, RiskPolicy? RiskPolicy = null,
    int StaleAfterSeconds = 65);

public sealed class UtcTimestampConverter : JsonConverter<DateTimeOffset>
{
    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.String ||
            !DateTimeOffset.TryParse(reader.GetString(), CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind, out var result))
            throw new JsonException("Invalid event timestamp");
        return result;
    }
    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options)
        => writer.WriteStringValue(value.UtcDateTime.ToString("O", CultureInfo.InvariantCulture));
}

public static class PlatformJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new UtcTimestampConverter() },
    };
}
