using System.Collections.Concurrent;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using static Sentinel.Monitor.Instances.SchedulerInventoryClient;

namespace Sentinel.Monitor.Instances;

public sealed record InstanceObservation(string InstanceId, string SchedulerStatus, string Track,
    string Status, string ReasonCode, string RuntimePhase, DateTimeOffset? RuntimeObservedAt,
    DateTimeOffset? ExpiresAt, bool TtlExpired, string ChallengeHealth, string RecoveryAction,
    FunctionalObservation? FunctionalCheck = null,
    DateTimeOffset? DeletionVerifiedAt = null, DateTimeOffset? DeletionRecheckAt = null);

public sealed record InstanceInventorySnapshot(string Status, string ReasonCode, string Coverage,
    bool CompleteInventory, DateTimeOffset? CheckedAt, IReadOnlyList<InstanceObservation> Instances);

// Only configured control-plane URLs are queried; service_url from a workload is never followed
public sealed class InstanceInventory(HttpClient http, IOptions<InstanceInventoryOptions> options,
    TimeProvider? timeProvider = null)
{
    private readonly InstanceInventoryOptions _options = options.Value;
    private readonly TimeProvider _clock = timeProvider ?? TimeProvider.System;
    private readonly SemaphoreSlim _refresh = new(1, 1);
    private const int DeletionRecheckSeconds = 300;
    private sealed record DeletionBinding(SchedulerRow Row, string BaseUrl, string TokenEnv, string CredentialDigest);
    private sealed record ConfirmedDeletion(DeletionBinding Binding, InstanceObservation Observation);
    private readonly ConcurrentDictionary<Guid, ConfirmedDeletion> _deletions = new();
    private InstanceInventorySnapshot _snapshot = new("PENDING", "NOT_CHECKED_YET", "all_pages", false, null, []);
    private static readonly HashSet<string> TerminalStates = ["STOPPED", "FAILED", "EXPIRED", "CLEANED"];

    public InstanceInventorySnapshot Snapshot()
    {
        if (!_options.Enabled) return new("DISABLED", "INSTANCE_MONITOR_DISABLED", "all_pages", false, null, []);
        var snapshot = Volatile.Read(ref _snapshot);
        // Account for the full list and four concurrent runtime checks, including functional probes
        var batches = (snapshot.Instances.Count + 3) / 4;
        var staleAfter = 2L * _options.PollIntervalSeconds + ScanTimeoutSeconds
            + ((long)batches + _options.FunctionalChecks.Count) * _options.TimeoutSeconds;
        if (snapshot.CheckedAt is { } at && (_clock.GetUtcNow() - at).TotalSeconds > staleAfter)
            return snapshot with { Status = "STALE", ReasonCode = "INVENTORY_REFRESH_STALE", CompleteInventory = false,
                Instances = snapshot.Instances.Select(row => Stale(row, "INVENTORY_REFRESH_STALE")).ToArray() };
        return snapshot with { Instances = snapshot.Instances.Select(row =>
            row.Status == "REMOVED" && row.DeletionVerifiedAt is not null
                ? DeletionFresh(row) ? row : Stale(row, "DELETION_RECHECK_STALE")
                : row.RuntimeObservedAt is { } observed && Math.Abs((_clock.GetUtcNow() - observed).TotalSeconds) > 60
                ? Stale(row, "STALE_RUNTIME_OBSERVATION")
                : row).ToArray() };
    }

    private static InstanceObservation Stale(InstanceObservation row, string reason) => row with
    {
        Status = "STALE", ReasonCode = reason, RecoveryAction = "manual_review",
        ChallengeHealth = row.FunctionalCheck is null ? "UNVERIFIED" : "STALE",
        FunctionalCheck = row.FunctionalCheck is { } check ? check with { Status = "STALE", ReasonCode = reason } : null,
        DeletionVerifiedAt = null, DeletionRecheckAt = null,
    };

    private bool DeletionFresh(InstanceObservation row) => row.DeletionVerifiedAt is { } at
        && (_clock.GetUtcNow() - at).TotalSeconds is >= -5 and < DeletionRecheckSeconds;

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        if (!_options.Enabled) { _deletions.Clear(); return; }
        await _refresh.WaitAsync(cancellationToken);
        try
        {
            if (!_options.IsValid()) { _deletions.Clear(); Publish("UNAVAILABLE", "INVALID_CONFIGURATION", []); return; }
            var result = await SchedulerInventoryClient.ReadAsync(http, _options.SchedulerUrl,
                _options.SchedulerTokenEnv, _options.TimeoutSeconds, cancellationToken);
            if (!result.Success) { _deletions.Clear(); Publish("UNAVAILABLE", result.Reason, []); return; }
            var rows = result.Rows;
            var currentIds = rows.Select(row => row.InstanceId).ToHashSet();
            foreach (var id in _deletions.Keys)
                if (!currentIds.Contains(id)) _deletions.TryRemove(id, out _);
            var observations = new InstanceObservation[rows.Count];
            await Parallel.ForEachAsync(Enumerable.Range(0, rows.Count),
                new ParallelOptions { MaxDegreeOfParallelism = 4, CancellationToken = cancellationToken },
                async (index, token) => observations[index] = await Observe(rows[index], token));
            Publish("COMPLETE", rows.Count == 0 ? "INVENTORY_EMPTY" : "ALL_PAGES_READ", observations);

        }
        finally { _refresh.Release(); }
    }

    private void Publish(string status, string reason, IReadOnlyList<InstanceObservation> rows) =>
        Volatile.Write(ref _snapshot, new(status, reason, "all_pages", status == "COMPLETE", _clock.GetUtcNow(), rows));

    private async Task<InstanceObservation> Observe(SchedulerRow row, CancellationToken token)
    {
        var active = !TerminalStates.Contains(row.Status);
        var expired = active && row.ExpiresAt <= _clock.GetUtcNow();
        var observation = new InstanceObservation(row.InstanceId.ToString(), row.Status, "UNMAPPED",
            "UNVERIFIED", "RUNTIME_TARGET_NOT_CONFIGURED", "UNKNOWN", null, row.ExpiresAt,
            expired, "UNVERIFIED", "manual_review");
        var target = _options.RuntimeTargets.SingleOrDefault(t => t.TargetId == row.TargetId);
        if (target is null || row.RuntimeType != "KUBERNETES")
        { _deletions.TryRemove(row.InstanceId, out _); return observation; }
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
            Environment.GetEnvironmentVariable(target.AuthTokenEnv) ?? "")));
        var binding = new DeletionBinding(row, target.BaseUrl, target.AuthTokenEnv, digest);
        if (!active && _deletions.TryGetValue(row.InstanceId, out var deleted)
            && deleted.Binding == binding && DeletionFresh(deleted.Observation))
            return deleted.Observation;
        // Failed rechecks must not fall back to a previous deletion confirmation.
        _deletions.TryRemove(row.InstanceId, out _);
        var url = target.BaseUrl.TrimEnd('/') + "/internal/v1/instances/" + row.InstanceId + "/runtime-status";
        var payload = await GetJson(url, target.AuthTokenEnv, token);
        using var document = payload.Document;
        if (document is null) return observation with { ReasonCode = payload.Reason };
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !Guid.TryParse(Text(root, "instance_id"), out var id)
            || id != row.InstanceId || Text(root, "target_id") != target.TargetId
            || string.IsNullOrWhiteSpace(Text(root, "runtime_workload_id"))
            || (row.WorkloadId is not null && Text(root, "runtime_workload_id") != row.WorkloadId))
            return observation with { ReasonCode = "RUNTIME_IDENTITY_MISMATCH" };
        var at = Timestamp(root, "observed_at");
        if (at is null || Math.Abs((_clock.GetUtcNow() - at.Value).TotalSeconds) > 60)
            return observation with { Status = "STALE", ReasonCode = "STALE_RUNTIME_OBSERVATION" };
        var phase = Text(root, "phase");
        if (phase is not ("READY" or "PROVISIONING" or "DEGRADED" or "TERMINATING" or "TERMINATED"))
            return observation with { ReasonCode = "INVALID_RUNTIME_PHASE" };
        observation = observation with { RuntimeObservedAt = at, RuntimePhase = phase };
        if (phase == "TERMINATED")
        {
            var removed = observation with { Status = active ? "DOWN" : "REMOVED",
                ReasonCode = active ? "ACTIVE_WORKLOAD_TERMINATED" : "RUNTIME_DELETION_CONFIRMED",
                RecoveryAction = active ? "manual_review" : "none",
                DeletionVerifiedAt = active ? null : at,
                DeletionRecheckAt = active ? null : at.Value.AddSeconds(DeletionRecheckSeconds) };
            if (!active && DeletionFresh(removed)) _deletions[row.InstanceId] = new(binding, removed);
            return removed;
        }
        var result = RuntimeProbeContract.Check(root, new PlatformTarget { Kind = "runtime_status" }, _clock.GetUtcNow());
        if (result.Status != "HEALTHY") return observation with { Status = result.Status, ReasonCode = result.Reason };
        if (!active) return observation with { Status = "DOWN", ReasonCode = "TERMINAL_RESOURCE_PRESENT" };
        if (expired) return observation with { Status = "DOWN", ReasonCode = "TTL_EXPIRED_RESOURCE_PRESENT" };
        var check = _options.FunctionalChecks.SingleOrDefault(c => c.InstanceId == row.InstanceId);
        if (check is not null)
        {
            if (check.ChallengeId != row.ChallengeId || check.TargetId != row.TargetId
                || check.RuntimeWorkloadId != row.WorkloadId || check.RuntimeWorkloadId != Text(root, "runtime_workload_id"))
                return observation with { ReasonCode = "FUNCTIONAL_BINDING_MISMATCH" };
            var resultCheck = await LocalFunctionalProbe.Check(http, check, _options.TimeoutSeconds, _clock, token);
            return observation with
            {
                Status = resultCheck.Status == "UP" ? "READY" : resultCheck.Status == "DOWN" ? "DOWN" : "UNVERIFIED",
                ReasonCode = resultCheck.Status == "UP" ? "RUNTIME_READY_FUNCTIONAL_CHECK_PASSED" :
                    resultCheck.Status == "DOWN" ? "FUNCTIONAL_CHECK_FAILED" : "FUNCTIONAL_CHECKER_ERROR",
                ChallengeHealth = resultCheck.Status, FunctionalCheck = resultCheck,
                RecoveryAction = resultCheck.Status == "UP" ? "none" : "manual_review",
            };
        }
        // Workload readiness is separate from functional health
        return observation with { Status = "READY", ReasonCode = "RUNTIME_READY_HEALTHCHECK_UNVERIFIED", RecoveryAction = "none" };
    }

    private async Task<(JsonDocument? Document, string Reason)> GetJson(string url, string tokenEnv, CancellationToken token)
    {
        var credential = Environment.GetEnvironmentVariable(tokenEnv);
        if (string.IsNullOrWhiteSpace(credential)) return (null, "AUTH_TOKEN_NOT_CONFIGURED");
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(_options.TimeoutSeconds));
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", credential);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        try
        {
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
            var code = (int)response.StatusCode;
            if (code != 200) return (null, code switch { 401 or 403 => "AUTH_REQUIRED", 404 => "RUNTIME_OR_ENDPOINT_NOT_FOUND",
                >= 300 and < 400 => "REDIRECT_BLOCKED", _ => "UPSTREAM_UNAVAILABLE" });
            if (response.Content.Headers.ContentType?.MediaType != "application/json") return (null, "EXPECTED_JSON");
            const int limit = 262144;
            if (response.Content.Headers.ContentLength > limit) return (null, "RESPONSE_TOO_LARGE");
            await using var input = await response.Content.ReadAsStreamAsync(deadline.Token);
            using var body = new MemoryStream();
            var buffer = new byte[4096];
            int count;
            while ((count = await input.ReadAsync(buffer, deadline.Token)) > 0)
            {
                if (body.Length + count > limit) return (null, "RESPONSE_TOO_LARGE");
                body.Write(buffer, 0, count);
            }
            body.Position = 0;
            return (await JsonDocument.ParseAsync(body, cancellationToken: deadline.Token), "OK");
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested) { return (null, "UPSTREAM_TIMEOUT"); }
        catch (HttpRequestException) { return (null, "UPSTREAM_UNAVAILABLE"); }
        catch (IOException) { return (null, "UPSTREAM_UNAVAILABLE"); }
        catch (JsonException) { return (null, "INVALID_JSON"); }
    }

    private static string? Text(JsonElement root, string field) => root.TryGetProperty(field, out var value)
        && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
    private static DateTimeOffset? Timestamp(JsonElement root, string field) => root.TryGetProperty(field, out var value)
        && value.ValueKind == JsonValueKind.String && value.TryGetDateTimeOffset(out var at) ? at : null;
}
