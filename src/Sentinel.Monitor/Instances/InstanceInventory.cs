using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;

namespace Sentinel.Monitor.Instances;

public sealed record InstanceObservation(string InstanceId, string SchedulerStatus, string Track,
    string Status, string ReasonCode, string RuntimePhase, DateTimeOffset? RuntimeObservedAt,
    DateTimeOffset? ExpiresAt, bool TtlExpired, string ChallengeHealth, string RecoveryAction,
    FunctionalObservation? FunctionalCheck = null);

public sealed record InstanceInventorySnapshot(string Status, string ReasonCode, string Coverage,
    bool CompleteInventory, DateTimeOffset? CheckedAt, IReadOnlyList<InstanceObservation> Instances);

// Only configured control-plane URLs are queried; service_url from a workload is never followed
public sealed class InstanceInventory(HttpClient http, IOptions<InstanceInventoryOptions> options,
    TimeProvider? timeProvider = null)
{
    private readonly InstanceInventoryOptions _options = options.Value;
    private readonly TimeProvider _clock = timeProvider ?? TimeProvider.System;
    private readonly SemaphoreSlim _refresh = new(1, 1);
    private InstanceInventorySnapshot _snapshot = new("PENDING", "NOT_CHECKED_YET", "recent_20", false, null, []);
    private static readonly HashSet<string> States = ["REQUESTED", "SCHEDULING", "PROVISIONING", "RUNNING",
        "RESTARTING", "RESETTING", "STOPPING", "STOPPED", "FAILED", "EXPIRED", "CLEANUP_PENDING", "CLEANED"];
    private static readonly HashSet<string> TerminalStates = ["STOPPED", "FAILED", "EXPIRED", "CLEANED"];

    public InstanceInventorySnapshot Snapshot()
    {
        if (!_options.Enabled) return new("DISABLED", "INSTANCE_MONITOR_DISABLED", "recent_20", false, null, []);
        var snapshot = Volatile.Read(ref _snapshot);
        // Polling may visit 20 rows in five batches; include the bounded batch duration
        var staleAfter = 2 * _options.PollIntervalSeconds + (6 + (_options.FunctionalChecks.Count > 0 ? 5 : 0)) * _options.TimeoutSeconds;
        if (snapshot.CheckedAt is { } at && (_clock.GetUtcNow() - at).TotalSeconds > staleAfter)
            return snapshot with { Status = "STALE", ReasonCode = "INVENTORY_REFRESH_STALE",
                Instances = snapshot.Instances.Select(row => Stale(row, "INVENTORY_REFRESH_STALE")).ToArray() };
        return snapshot with { Instances = snapshot.Instances.Select(row =>
            row.RuntimeObservedAt is { } observed && Math.Abs((_clock.GetUtcNow() - observed).TotalSeconds) > 60
                ? Stale(row, "STALE_RUNTIME_OBSERVATION")
                : row).ToArray() };
    }

    private static InstanceObservation Stale(InstanceObservation row, string reason) => row with
    {
        Status = "STALE", ReasonCode = reason, RecoveryAction = "manual_review",
        ChallengeHealth = row.FunctionalCheck is null ? "UNVERIFIED" : "STALE",
        FunctionalCheck = row.FunctionalCheck is { } check ? check with { Status = "STALE", ReasonCode = reason } : null,
    };

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        if (!_options.Enabled) return;
        await _refresh.WaitAsync(cancellationToken);
        try
        {
            if (!_options.IsValid()) { Publish("UNAVAILABLE", "INVALID_CONFIGURATION", []); return; }
            var payload = await GetJson(_options.SchedulerUrl, _options.SchedulerTokenEnv, cancellationToken);
            using var document = payload.Document;
            if (document is null) { Publish("UNAVAILABLE", payload.Reason, []); return; }
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || Text(root, "code") != "SUCCESS"
                || !root.TryGetProperty("data", out var data) || data.ValueKind != JsonValueKind.Array
                || data.GetArrayLength() > 20)
            { Publish("UNAVAILABLE", "INVALID_SCHEDULER_INVENTORY", []); return; }
            var rows = new List<SchedulerRow>();
            var ids = new HashSet<Guid>();
            foreach (var item in data.EnumerateArray())
            {
                if (!TryRow(item, out var row) || !ids.Add(row!.InstanceId))
                { Publish("UNAVAILABLE", "INVALID_SCHEDULER_INVENTORY", []); return; }
                rows.Add(row);
            }
            using var concurrency = new SemaphoreSlim(4);
            var observations = await Task.WhenAll(rows.Select(async row =>
            {
                await concurrency.WaitAsync(cancellationToken);
                try { return await Observe(row, cancellationToken); }
                finally { concurrency.Release(); }
            }));
            Publish("PARTIAL", rows.Count == 0 ? "RECENT_WINDOW_EMPTY" : "RECENT_WINDOW_ONLY", observations);
        }
        finally { _refresh.Release(); }
    }

    private void Publish(string status, string reason, IReadOnlyList<InstanceObservation> rows) =>
        Volatile.Write(ref _snapshot, new(status, reason, "recent_20", false, _clock.GetUtcNow(), rows));

    private async Task<InstanceObservation> Observe(SchedulerRow row, CancellationToken token)
    {
        var active = !TerminalStates.Contains(row.Status);
        var expired = active && row.ExpiresAt <= _clock.GetUtcNow();
        var observation = new InstanceObservation(row.InstanceId.ToString(), row.Status, "UNMAPPED",
            "UNVERIFIED", "RUNTIME_TARGET_NOT_CONFIGURED", "UNKNOWN", null, row.ExpiresAt,
            expired, "UNVERIFIED", "manual_review");
        var target = _options.RuntimeTargets.SingleOrDefault(t => t.TargetId == row.TargetId);
        if (target is null || row.RuntimeType != "KUBERNETES") return observation;
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
            return observation with { Status = active ? "DOWN" : "REMOVED",
                ReasonCode = active ? "ACTIVE_WORKLOAD_TERMINATED" : "RUNTIME_DELETION_CONFIRMED",
                RecoveryAction = active ? "manual_review" : "none" };
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

    private sealed record SchedulerRow(Guid InstanceId, string Status, string? RuntimeType, string? TargetId,
        string? WorkloadId, DateTimeOffset ExpiresAt, Guid? ChallengeId);
    private static bool TryRow(JsonElement row, out SchedulerRow? parsed)
    {
        parsed = null;
        if (row.ValueKind != JsonValueKind.Object || !Guid.TryParse(Text(row, "instance_id"), out var id)
            || id == Guid.Empty || !States.Contains(Text(row, "status") ?? "")
            || Timestamp(row, "expires_at") is not { } expiry || Timestamp(row, "hard_expires_at") is not { } hard)
            return false;
        foreach (var field in new[] { "runtime_type", "runtime_target_id", "runtime_workload_id" })
            if (row.TryGetProperty(field, out var value) && value.ValueKind is not (JsonValueKind.Null or JsonValueKind.String)) return false;
        var type = Text(row, "runtime_type");
        if (type is not (null or "KUBERNETES" or "VM")) return false;
        var idle = Timestamp(row, "idle_expires_at");
        if (row.TryGetProperty("idle_expires_at", out var idleValue)
            && idleValue.ValueKind != JsonValueKind.Null && idle is null) return false;
        expiry = expiry < hard ? expiry : hard;
        if (idle is { } idleAt && idleAt < expiry) expiry = idleAt;
        Guid? challengeId = null;
        if (row.TryGetProperty("challenge_id", out var challenge) && challenge.ValueKind != JsonValueKind.Null)
        {
            if (challenge.ValueKind != JsonValueKind.String || !Guid.TryParse(challenge.GetString(), out var parsedChallenge)
                || parsedChallenge == Guid.Empty) return false;
            challengeId = parsedChallenge;
        }
        parsed = new(id, Text(row, "status")!, type, Text(row, "runtime_target_id"), Text(row, "runtime_workload_id"), expiry, challengeId);
        return true;
    }
    private static string? Text(JsonElement root, string field) => root.TryGetProperty(field, out var value)
        && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
    private static DateTimeOffset? Timestamp(JsonElement root, string field) => root.TryGetProperty(field, out var value)
        && value.ValueKind == JsonValueKind.String && value.TryGetDateTimeOffset(out var at) ? at : null;
}
