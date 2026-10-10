using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;

namespace Sentinel.Monitor.Recovery;

public sealed class RecoveryCoordinator
{
    private readonly RecoveryOptions _options;
    private readonly PlatformOptions _platform;
    private readonly RecoveryBinding[] _bindings;
    private readonly TimeProvider _clock;
    private readonly object _gate = new();
    private RecoveryJournal _journal = new();
    private bool _storageHealthy = true;
    private DateTimeOffset? _executorSeenAt;
    private IReadOnlyList<ProbeObservation> _latest = [];
    private bool _monitorStorageHealthy;
    private DateTimeOffset? _inspectionReceivedAt;
    private HashSet<string> _confirmedStopped = [];

    public RecoveryCoordinator(IOptions<RecoveryOptions> options, IOptions<PlatformOptions> platform,
        TimeProvider? clock = null)
    {
        _options = options.Value;
        _platform = platform.Value;
        _clock = clock ?? TimeProvider.System;
        if (!_options.IsValid(_platform)) throw new ArgumentException("Invalid recovery binding");
        _bindings = _options.Enabled ? RecoveryBindings.All.Where(binding => _options.EffectiveAssets.Contains(binding.AssetId)).ToArray() : [];
        if (!_options.Enabled) return;
        try
        {
            if (File.Exists(_options.StateFile))
            {
                if (new FileInfo(_options.StateFile).Length > 262144) throw new JsonException();
                _journal = JsonSerializer.Deserialize<RecoveryJournal>(File.ReadAllText(_options.StateFile), PlatformJson.Options)
                    ?? throw new JsonException();
                if (!ValidJournal()) throw new JsonException();
                if (_journal.SchemaVersion == 1)
                    _journal.Assets[RecoveryOptions.AssetId] = new() { LastRequestedAt = _journal.LastRequestedAt };
                _journal.SchemaVersion = 2;
            }
            foreach (var binding in RecoveryBindings.All) _journal.Assets.TryAdd(binding.AssetId, new());
            // A restart requires fresh baselines; consumed claims are never reissued.
            foreach (var state in _journal.Assets.Values)
            {
                state.Failures = state.Successes = 0;
                state.Armed = false;
                state.LastObservedAt = null;
                state.LastVerifiedAt = null;
            }
            foreach (var request in _journal.Requests.Where(request => request.Status == "QUEUED").ToArray())
                Replace(request with { Status = "CANCELLED", ReasonCode = "MONITOR_RESTARTED", CompletedAt = _clock.GetUtcNow() });
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            _journal = new();
            _storageHealthy = false;
        }
    }

    public string Mode => _options.Enabled ? "automatic_local" : "observe_only";

    public RecoverySnapshot Snapshot()
    {
        lock (_gate) return new(_options.Enabled, _storageHealthy, ExecutorConnected(), _executorSeenAt,
            RecoveryOptions.AssetId, RecoveryOptions.Action, _journal.Assets.GetValueOrDefault(RecoveryOptions.AssetId)?.Armed ?? false,
            1, _options.CooldownSeconds, _journal.Requests.AsEnumerable().Reverse().ToArray(),
            _bindings.Select(binding => {
                var state = _journal.Assets.GetValueOrDefault(binding.AssetId) ?? new();
                return new RecoveryAssetSnapshot(binding.AssetId, binding.Track, binding.Service, state.Armed,
                    state.Failures, state.Successes, binding.DependencyTargets, binding.VerificationTargets);
            }).ToArray());
    }

    public void Observe(PlatformSnapshot snapshot)
    {
        lock (_gate)
        {
            if (!_options.Enabled || !_storageHealthy) return;
            var now = _clock.GetUtcNow();
            var changed = false;
            _monitorStorageHealthy = snapshot.EventStorageHealthy;
            _latest = snapshot.Targets;
            var active = Active();
            if (active is not null)
            {
                var deadline = active.Status == "VERIFYING" ? active.VerificationDeadline : active.ExpiresAt;
                if (deadline <= now)
                {
                    Replace(active with { Status = "EXPIRED", ReasonCode = "RECOVERY_DEADLINE_EXCEEDED", CompletedAt = now });
                    changed = true;
                }
                else if (active.Status == "QUEUED" && !EligibleObservation(active.AssetId))
                {
                    Replace(active with { Status = "CANCELLED", ReasonCode = "EVIDENCE_CHANGED", CompletedAt = now });
                    changed = true;
                }
            }

            foreach (var binding in _bindings)
            {
                var state = _journal.Assets[binding.AssetId];
                var observation = Reading(binding.AssetId);
                if (!Fresh(observation) || !_monitorStorageHealthy)
                {
                    changed |= state.Failures != 0 || state.Successes != 0 || state.Armed;
                    state.Failures = state.Successes = 0;
                    state.Armed = false;
                    continue;
                }
                if (state.LastObservedAt is not null && observation!.CheckedAt <= state.LastObservedAt) continue;
                state.LastObservedAt = observation!.CheckedAt;
                changed = true;
                active = Active();
                var verifying = active?.AssetId == binding.AssetId && active.Status == "VERIFYING";
                var after = verifying ? active!.VerificationDeadline!.Value.AddSeconds(-_options.VerificationTimeoutSeconds) : (DateTimeOffset?)null;
                var healthy = Healthy(binding.AssetId, after)
                    && binding.VerificationTargets.All(id => Healthy(id, after));
                if (!healthy) state.Successes = 0;
                else
                {
                    var verifiedAt = binding.VerificationTargets.Append(binding.AssetId).Min(id => Reading(id)!.CheckedAt!.Value);
                    if (state.LastVerifiedAt is null || verifiedAt > state.LastVerifiedAt)
                    {
                        state.Successes = Math.Min(state.Successes + 1, 1000);
                        state.LastVerifiedAt = verifiedAt;
                    }
                }
                state.Failures = EligibleObservation(binding.AssetId) ? Math.Min(state.Failures + 1, 1000) : 0;
                if (verifying && state.Successes >= _platform.RecoveryThreshold)
                    Replace(active! with { Status = "SUCCEEDED", ReasonCode = "FUNCTION_VERIFIED", CompletedAt = now });
                if (healthy && Active()?.AssetId != binding.AssetId && state.Successes >= _platform.RecoveryThreshold
                    && (state.LastRequestedAt is null || now - state.LastRequestedAt >= TimeSpan.FromSeconds(_options.CooldownSeconds)))
                    state.Armed = true;
            }

            // Only one mutation is active globally; dependency baselines order cascade recovery.
            if (Active() is null && ExecutorConnected())
            {
                var binding = _bindings.FirstOrDefault(binding => _journal.Assets[binding.AssetId].Armed
                    && _journal.Assets[binding.AssetId].Failures >= _platform.FailureThreshold && EligibleObservation(binding.AssetId));
                if (binding is not null)
                {
                    _journal.Requests.Add(new(Guid.NewGuid().ToString(), binding.AssetId, binding.Track,
                        RecoveryOptions.ExecutorId, RecoveryOptions.Action, now,
                        now.AddSeconds(_options.RequestLifetimeSeconds), "QUEUED",
                        Reading(binding.AssetId)!.ReasonCode == "TIMEOUT" ? "CONFIRMED_STOPPED_TIMEOUT" : "CONFIRMED_CONNECTION_FAILURE"));
                    var state = _journal.Assets[binding.AssetId];
                    state.LastRequestedAt = now;
                    state.Armed = false;
                    while (_journal.Requests.Count > 100)
                    {
                        _journal.Claims.Remove(_journal.Requests[0].RequestId);
                        _journal.Requests.RemoveAt(0);
                    }
                    changed = true;
                }
            }
            if (changed) Save();
        }
    }

    public bool Inspect(RecoveryInspection inspection)
    {
        lock (_gate)
        {
            if (!_options.Enabled || !_storageHealthy || inspection.ExecutorId != RecoveryOptions.ExecutorId
                || inspection.Assets is null || inspection.Assets.Count > RecoveryBindings.All.Count
                || inspection.Assets.Any(item => item is null || !RecoveryBindings.All.Any(binding => binding.AssetId == item.AssetId))
                || inspection.Assets.Select(item => item.AssetId).Distinct().Count() != inspection.Assets.Count) return false;
            // Server receipt time is authoritative; the host cannot extend an attestation by sending a future timestamp.
            _inspectionReceivedAt = _clock.GetUtcNow();
            _confirmedStopped = inspection.Assets.Where(item => item.StoppedEligible).Select(item => item.AssetId).ToHashSet();
            return true;
        }
    }

    public RecoveryRequest? Poll()
    {
        lock (_gate)
        {
            if (!_options.Enabled || !_storageHealthy) return null;
            _executorSeenAt = _clock.GetUtcNow();
            return _journal.Requests.FirstOrDefault(request => request.Status == "QUEUED"
                && request.ExpiresAt > _clock.GetUtcNow() && EligibleObservation(request.AssetId));
        }
    }

    public ClaimedRecovery? Claim(string requestId, string executorId)
    {
        lock (_gate)
        {
            if (!_options.Enabled || !_storageHealthy || executorId != RecoveryOptions.ExecutorId) return null;
            var request = _journal.Requests.SingleOrDefault(item => item.RequestId == requestId);
            if (request is null || request.Status != "QUEUED" || request.ExpiresAt <= _clock.GetUtcNow()
                || !EligibleObservation(request.AssetId)) return null;
            var claimId = Guid.NewGuid().ToString();
            var claimed = request with { Status = "CLAIMED", ClaimedAt = _clock.GetUtcNow(), ReasonCode = "EXECUTOR_CLAIMED" };
            _journal.Claims[requestId] = claimId;
            Replace(claimed);
            return Save() ? new(claimed, claimId) : null;
        }
    }

    public bool Complete(string requestId, RecoveryResult result)
    {
        lock (_gate)
        {
            if (!_options.Enabled || !_storageHealthy || result.ExecutorId != RecoveryOptions.ExecutorId
                || !_journal.Claims.TryGetValue(requestId, out var claimId) || claimId != result.ClaimId
                || result.ResultCode is not ("CONTAINER_STARTED" or "PRECONDITION_NOT_MET" or "EXECUTION_FAILED" or "OUTCOME_UNKNOWN")) return false;
            var request = _journal.Requests.SingleOrDefault(item => item.RequestId == requestId);
            if (request is null) return false;
            if (request.ExecutionResultCode is not null) return request.ExecutionResultCode == result.ResultCode;
            var now = _clock.GetUtcNow();
            if (request.Status != "CLAIMED" || request.ExpiresAt <= now) return false;
            var started = result.ResultCode == "CONTAINER_STARTED";
            Replace(request with { Status = started ? "VERIFYING" : "BLOCKED", ReasonCode = result.ResultCode,
                ExecutionResultCode = result.ResultCode,
                VerificationDeadline = started ? now.AddSeconds(_options.VerificationTimeoutSeconds) : null,
                CompletedAt = started ? null : now });
            _journal.Assets[request.AssetId].Successes = 0;
            return Save();
        }
    }

    private bool ExecutorConnected() => _executorSeenAt is { } seen && _clock.GetUtcNow() - seen <= TimeSpan.FromSeconds(15);
    private ProbeObservation? Reading(string id) => _latest.SingleOrDefault(item => item.TargetId == id);
    private bool Fresh(ProbeObservation? observation) => observation is { CheckedAt: { } at, Enabled: true }
        && _platform.Targets.Any(target => target.TargetId == observation.TargetId && target.Enabled
            && target.Track == observation.Track && target.Kind == observation.Kind && target.Url == observation.Url)
        && at <= _clock.GetUtcNow() && _clock.GetUtcNow() - at <= TimeSpan.FromSeconds(2 * _platform.PollIntervalSeconds + _platform.TimeoutSeconds + 5);
    private bool Healthy(string id, DateTimeOffset? after = null)
    {
        var reading = Reading(id);
        return Fresh(reading) && reading!.Status == "HEALTHY"
            && reading.ReasonCode == RecoveryBindings.HealthyReasonFor(id)
            && (after is null || reading.CheckedAt > after);
    }
    private bool EligibleObservation(string id)
    {
        var binding = _bindings.FirstOrDefault(item => item.AssetId == id);
        var reading = Reading(id);
        var stoppedConfirmed = _confirmedStopped.Contains(id)
            && _inspectionReceivedAt is { } inspected && _clock.GetUtcNow() - inspected <= TimeSpan.FromSeconds(15);
        return binding is not null && _monitorStorageHealthy && Fresh(reading)
            && reading!.Status == "DOWN" && (reading.ReasonCode == "CONNECTION_FAILED"
                || (reading.ReasonCode == "TIMEOUT" && stoppedConfirmed))
            // Runtime can exit when its store disconnects. Its API cannot prove
            // the store is back, so do not consume an incident before the host
            // confirms both the stopped Runtime and the healthy store container.
            && (binding.Kind != "runtime_status" || stoppedConfirmed)
            && binding.DependencyTargets.All(required => Healthy(required));
    }
    private RecoveryRequest? Active() => _journal.Requests.LastOrDefault(request => request.Status is "QUEUED" or "CLAIMED" or "VERIFYING");
    private void Replace(RecoveryRequest request) => _journal.Requests[_journal.Requests.FindIndex(item => item.RequestId == request.RequestId)] = request;

    private bool Save()
    {
        try
        {
            var path = Path.GetFullPath(_options.StateFile);
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            using (var stream = new FileStream(path + ".tmp", FileMode.Create, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(stream, _journal, PlatformJson.Options);
                stream.Flush(flushToDisk: true);
            }
            File.Move(path + ".tmp", path, overwrite: true);
            return true;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            _storageHealthy = false;
            return false;
        }
    }

    private bool ValidJournal() => _journal.SchemaVersion is 1 or 2 && _journal.Requests is not null && _journal.Claims is not null
        && _journal.Assets is not null && _journal.Assets.All(pair => pair.Value is not null
            && RecoveryBindings.All.Any(binding => binding.AssetId == pair.Key)
            && pair.Value.Failures is >= 0 and <= 1000 && pair.Value.Successes is >= 0 and <= 1000)
        && _journal.Requests.Count <= 100 && _journal.Requests.All(item => item is not null)
        && _journal.Requests.Select(item => item.RequestId).Distinct().Count() == _journal.Requests.Count
        && _journal.Requests.Count(item => item.Status is "QUEUED" or "CLAIMED" or "VERIFYING") <= 1
        && _journal.Requests.All(item => Guid.TryParse(item.RequestId, out _)
            && RecoveryBindings.All.Any(binding => binding.AssetId == item.AssetId && binding.Track == item.Track)
            && (_journal.SchemaVersion != 1 || item.AssetId == RecoveryOptions.AssetId)
            && item.ExecutorId == RecoveryOptions.ExecutorId && item.Action == RecoveryOptions.Action
            && item.CreatedAt != default && item.ExpiresAt > item.CreatedAt
            && item.Status is "QUEUED" or "CLAIMED" or "VERIFYING" or "SUCCEEDED" or "BLOCKED" or "CANCELLED" or "EXPIRED"
            && (item.Status != "VERIFYING" || item.VerificationDeadline is not null))
        && _journal.Claims.All(pair => Guid.TryParse(pair.Value, out _) && _journal.Requests.Any(item => item.RequestId == pair.Key));
}
