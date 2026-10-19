using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Instances;

namespace Sentinel.Monitor.Platform;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ChallengeHeartbeat(
    [property: JsonRequired] DateTimeOffset CheckedAt,
    [property: JsonRequired] string InstanceId, [property: JsonRequired] string ChallengeId,
    [property: JsonRequired] string ReleaseId, [property: JsonRequired] string OwnerTeamId,
    [property: JsonRequired] string RuntimeTargetId, [property: JsonRequired] string RuntimeWorkloadId,
    [property: JsonRequired] string Profile, [property: JsonRequired] string SourceRevision,
    [property: JsonRequired] string Scope, [property: JsonRequired] string Status,
    [property: JsonRequired] string ReasonCode, [property: JsonRequired] int ElapsedMs);

// A reporter credential is limited to one explicitly owned, expiring deployment test.
// Scheduler-discovered URLs never enter the API server's outbound HTTP client.
public sealed class ChallengeHeartbeatStore(IOptions<PlatformOptions> options, TimeProvider? clock = null)
{
    public const string SourceRevision = "1dea73b652f9eeb98204c1fda7f66b9afb89505d";
    public const int FreshSeconds = 90;
    private readonly TimeProvider _clock = clock ?? TimeProvider.System;
    private readonly object _gate = new();
    private readonly Dictionary<string, ChallengeHeartbeat> _latest = [];
    private static readonly Dictionary<string, string> SuccessReasons = new()
    {
        ["web-grade-tampering"] = "NOTICE_DATABASE_READ_CONFIRMED",
        ["web-notebook"] = "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED",
        ["pwn-random6"] = "VM_NONCE_ARITHMETIC_CONFIRMED",
        ["pwn-note"] = "NAME_ROUNDTRIP_AND_MESSAGE_CONFIRMED",
        ["pwn-ghost-file"] = "OWNED_NOTE_CREATE_EDIT_READ_DELETE_CONFIRMED",
        ["pwn-price-tag"] = "VALID_PRODUCT_UPDATE_AND_ACCESS_RULE_CONFIRMED",
    };
    private static readonly HashSet<string> DownReasons = ["FUNCTION_RESPONSE_MISMATCH", "FUNCTION_UNAVAILABLE", "FUNCTION_TIMEOUT",
        "FUNCTION_UNREACHABLE", "CONNECTION_CLOSED_BEFORE_RESPONSE"];
    private static readonly HashSet<string> ErrorReasons = ["ENDPOINT_BINDING_REJECTED", "SENSITIVE_RESPONSE_DISCARDED",
        "RESPONSE_TOO_LARGE", "INVALID_FUNCTION_RESPONSE", "CONTROL_PLANE_UNVERIFIED", "OWNERSHIP_BINDING_REJECTED",
        "RUNTIME_NOT_READY", "REDIRECT_BLOCKED", "RESPONSE_SIZE_INVALID", "TRUNCATED_RESPONSE"];
    private static readonly string[] BindingFields = ["instance_id", "challenge_id", "release_id", "owner_team_id",
        "runtime_target_id", "runtime_workload_id", "profile", "source_revision", "expires_at"];

    public static bool ValidBinding(IReadOnlyDictionary<string, string> fields) =>
        fields.Count == BindingFields.Length && BindingFields.All(fields.ContainsKey)
        && fields.Values.All(value => value is not null && value.Length <= 256)
        && new[] { "instance_id", "challenge_id", "release_id", "owner_team_id" }.All(key =>
            Guid.TryParseExact(fields[key], "D", out var id) && id != Guid.Empty)
        && Regex.IsMatch(fields["runtime_target_id"], "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
        && fields["runtime_workload_id"].Length is > 0 and <= 256
        && !fields["runtime_workload_id"].Any(char.IsControl)
        && SuccessReasons.ContainsKey(fields["profile"]) && fields["source_revision"] == SourceRevision
        && Regex.IsMatch(fields["expires_at"], "(Z|[+-][0-9]{2}:[0-9]{2})$")
        && DateTimeOffset.TryParse(fields["expires_at"], CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind, out _);

    private PlatformTarget? Target(string id) => options.Value.Targets.Find(t =>
        t.TargetId == id && t.Kind == "challenge_heartbeat" && t.Enabled && ValidBinding(t.ExpectedFields));
    private bool Expired(PlatformTarget target)
    {
        var expires = DateTimeOffset.Parse(target.ExpectedFields["expires_at"], CultureInfo.InvariantCulture);
        return expires <= _clock.GetUtcNow() || expires > _clock.GetUtcNow().AddHours(24);
    }

    public bool Authorize(string id, string authorization)
    {
        var target = Target(id);
        if (target?.AuthTokenEnv is null || Expired(target) || !authorization.StartsWith("Bearer ", StringComparison.Ordinal)) return false;
        var token = Environment.GetEnvironmentVariable(target.AuthTokenEnv);
        return token?.Length >= 32 && CryptographicOperations.FixedTimeEquals(
            SHA256.HashData(Encoding.UTF8.GetBytes(token)), SHA256.HashData(Encoding.UTF8.GetBytes(authorization[7..])));
    }

    public bool Accept(string id, ChallengeHeartbeat value)
    {
        var target = Target(id);
        if (target is null || Expired(target) || value.Scope != "owned_deployment_test"
            || value.CheckedAt > _clock.GetUtcNow().AddSeconds(30)
            || _clock.GetUtcNow() - value.CheckedAt > TimeSpan.FromSeconds(FreshSeconds)
            || value.ElapsedMs is < 0 or > 30000) return false;
        var fields = target.ExpectedFields;
        if (value.InstanceId != fields["instance_id"] || value.ChallengeId != fields["challenge_id"]
            || value.ReleaseId != fields["release_id"] || value.OwnerTeamId != fields["owner_team_id"]
            || value.RuntimeTargetId != fields["runtime_target_id"] || value.RuntimeWorkloadId != fields["runtime_workload_id"]
            || value.Profile != fields["profile"] || value.SourceRevision != fields["source_revision"]
            || !(value.Status == "UP" && value.ReasonCode == SuccessReasons[value.Profile]
                || value.Status == "DOWN" && DownReasons.Contains(value.ReasonCode)
                || value.Status == "CHECKER_ERROR" && ErrorReasons.Contains(value.ReasonCode))) return false;
        lock (_gate)
        {
            if (_latest.TryGetValue(id, out var old) && value.CheckedAt <= old.CheckedAt) return false;
            _latest[id] = value;
        }
        return true;
    }

    public ProbeObservation Read(PlatformTarget target)
    {
        if (!ValidBinding(target.ExpectedFields))
            return PlatformProbeClient.Result(target, "UNVERIFIED", "OWNERSHIP_BINDING_REJECTED", null, null, null);
        if (Expired(target)) return PlatformProbeClient.Result(target, "UNVERIFIED", "FUNCTIONAL_LEASE_EXPIRED", null, null, null);
        ChallengeHeartbeat? value;
        lock (_gate) _latest.TryGetValue(target.TargetId, out value);
        if (value is null) return PlatformProbeClient.Result(target, "PENDING", "FUNCTIONAL_REPORT_PENDING", null, null, null);
        var stale = _clock.GetUtcNow() - value.CheckedAt > TimeSpan.FromSeconds(FreshSeconds);
        return PlatformProbeClient.Result(target, stale ? "STALE" : value.Status switch
            { "UP" => "HEALTHY", "DOWN" => "DOWN", _ => "UNVERIFIED" },
            stale ? "FUNCTIONAL_REPORT_STALE" : value.ReasonCode, value.CheckedAt, null, null, new Dictionary<string, string>
            {
                ["instance_id"] = value.InstanceId, ["challenge_id"] = value.ChallengeId, ["release_id"] = value.ReleaseId,
                ["profile"] = value.Profile, ["scope"] = value.Scope, ["functional_status"] = stale ? "STALE" : value.Status,
                ["source_revision"] = value.SourceRevision,
            });
    }

    internal FunctionalObservation? ReadInstance(Guid instanceId, Guid? challengeId, Guid? releaseId, Guid? teamId,
        string? targetId, string? workloadId, DateTimeOffset expiresAt)
    {
        var target = options.Value.Targets.SingleOrDefault(t => t.Enabled && t.Kind == "challenge_heartbeat"
            && t.ExpectedFields.GetValueOrDefault("instance_id") == instanceId.ToString());
        if (target is null) return null;
        var fields = target.ExpectedFields;
        if (challengeId?.ToString() != fields["challenge_id"] || releaseId?.ToString() != fields["release_id"]
            || teamId?.ToString() != fields["owner_team_id"] || targetId != fields["runtime_target_id"]
            || workloadId != fields["runtime_workload_id"] || expiresAt < DateTimeOffset.Parse(fields["expires_at"], CultureInfo.InvariantCulture))
            return new(target.TargetId, "owned_deployment_test", "CHECKER_ERROR", "OWNERSHIP_BINDING_REJECTED", _clock.GetUtcNow());
        var observation = Read(target);
        return new(target.TargetId, "owned_deployment_test", observation.Status switch
            { "HEALTHY" => "UP", "DOWN" => "DOWN", "STALE" => "STALE", _ => "CHECKER_ERROR" },
            observation.ReasonCode, observation.CheckedAt ?? _clock.GetUtcNow());
    }
}
