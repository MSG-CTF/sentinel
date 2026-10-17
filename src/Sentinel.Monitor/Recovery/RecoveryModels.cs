using System.Text.Json.Serialization;

namespace Sentinel.Monitor.Recovery;

public sealed record RecoveryRequest(
    string RequestId, string AssetId, string Track, string ExecutorId, string Action,
    DateTimeOffset CreatedAt, DateTimeOffset ExpiresAt, string Status, string ReasonCode,
    DateTimeOffset? ClaimedAt = null, DateTimeOffset? VerificationDeadline = null,
    DateTimeOffset? CompletedAt = null, string? ExecutionResultCode = null);

public sealed record RecoverySnapshot(
    bool Enabled, bool StorageHealthy, bool ExecutorConnected, DateTimeOffset? ExecutorSeenAt,
    string AssetId, string Action, bool Armed, int AttemptLimit, int CooldownSeconds,
    IReadOnlyList<RecoveryRequest> Requests, IReadOnlyList<RecoveryAssetSnapshot> Assets);

public sealed record RecoveryAssetSnapshot(string AssetId, string Track, string Service, bool Armed,
    int Failures, int Successes, IReadOnlyList<string> DependencyTargets, IReadOnlyList<string> VerificationTargets);

public sealed record ClaimedRecovery(RecoveryRequest Request, string ClaimId);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RecoveryClaim(string ExecutorId);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RecoveryResult(string ExecutorId, string ClaimId, string ResultCode);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RecoveryInspection(string ExecutorId, IReadOnlyList<RecoveryInspectedAsset> Assets);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record RecoveryInspectedAsset(string AssetId, bool StoppedEligible);

// Claim credentials are persisted for crash recovery but never included in public status.
public sealed class RecoveryJournal
{
    public int SchemaVersion { get; set; } = 2;
    public Dictionary<string, RecoveryAssetState> Assets { get; set; } = [];
    // Legacy fields remain readable so upgrades preserve version 1 history.
    public bool Armed { get; set; }
    public DateTimeOffset? LastObservedAt { get; set; }
    public DateTimeOffset? LastRequestedAt { get; set; }
    public int Failures { get; set; }
    public int Successes { get; set; }
    public List<RecoveryRequest> Requests { get; set; } = [];
    public Dictionary<string, string> Claims { get; set; } = [];
}

public sealed class RecoveryAssetState
{
    public bool Armed { get; set; }
    public DateTimeOffset? LastObservedAt { get; set; }
    public DateTimeOffset? LastVerifiedAt { get; set; }
    public DateTimeOffset? LastRequestedAt { get; set; }
    public int Failures { get; set; }
    public int Successes { get; set; }
}
