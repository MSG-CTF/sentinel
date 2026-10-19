using System.Globalization;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Platform;

public sealed class PlatformMonitor
{
    private readonly PlatformOptions _options;
    private readonly PlatformProbeClient _probe;
    private readonly ILogger<PlatformMonitor> _logger;
    private readonly object _gate = new();
    private readonly Dictionary<string, ProbeObservation> _observations = [];
    private readonly Dictionary<string, (int Failures, int Successes, string Reported)> _streaks = [];
    private readonly Queue<MonitorEvent> _events = new();
    private readonly Queue<MonitorSample> _history = new();
    private readonly SemaphoreSlim _refresh = new(1, 1);
    private readonly TimeProvider _clock;
    private readonly DateTimeOffset _startedAt;
    private DateTimeOffset? _updatedAt;
    private bool _storageHealthy = true;
    private readonly RiskEvaluator _risk;

    public PlatformMonitor(IOptions<PlatformOptions> options, PlatformProbeClient probe, ILogger<PlatformMonitor> logger,
        TimeProvider? timeProvider = null)
    {
        _options = options.Value;
        _risk = new RiskEvaluator(_options.RiskPolicy, 2 * _options.PollIntervalSeconds + _options.TimeoutSeconds + 5);
        _probe = probe;
        _logger = logger;
        _clock = timeProvider ?? TimeProvider.System;
        _startedAt = _clock.GetUtcNow();
        foreach (var target in _options.Targets)
            _observations[target.TargetId] = PlatformProbeClient.Result(target,
                target.Enabled ? "PENDING" : "UNCONFIGURED",
                target.Enabled ? "NOT_CHECKED_YET" : "ENDPOINT_NOT_CONFIGURED", null, null, null);
        LoadEvents();
    }

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        await _refresh.WaitAsync(cancellationToken);
        try
        {
            using var concurrency = new SemaphoreSlim(4, 4);
            var results = await Task.WhenAll(_options.Targets.Select(async target =>
            {
                await concurrency.WaitAsync(cancellationToken);
                try { return await _probe.CheckAsync(target, _options.TimeoutSeconds, cancellationToken); }
                finally { concurrency.Release(); }
            }));
            lock (_gate)
            {
                foreach (var result in results)
                {
                    var previous = _observations[result.TargetId];
                    _observations[result.TargetId] = result with { Risk = _risk.Evaluate(result) };
                    // A cached readiness response is one measurement, including in event history.
                    if (result.Kind is not ("runtime_healthz" or "challenge_heartbeat") || previous.CheckedAt is null || result.CheckedAt > previous.CheckedAt)
                        ObserveTransition(result);
                }
                _updatedAt = _clock.GetUtcNow();
                _history.Enqueue(new MonitorSample(_updatedAt.Value, results.Select(result => new TargetSample(
                    result.TargetId, result.Status, result.LatencyMs)).ToArray()));
                while (_history.Count > 120) _history.Dequeue();
            }
        }
        finally { _refresh.Release(); }
    }

    public PlatformSnapshot Snapshot()
    {
        lock (_gate) return new(_options.Profile, "observe_only", _startedAt, _updatedAt,
            _options.PollIntervalSeconds, _options.FailureThreshold, _storageHealthy,
            _options.Targets.Select(target => CurrentObservation(target.TargetId)).ToArray(),
            RiskPolicy: _options.RiskPolicy, StaleAfterSeconds: 2 * _options.PollIntervalSeconds + _options.TimeoutSeconds + 5);
    }

    private ProbeObservation CurrentObservation(string targetId)
    {
        var result = _observations[targetId];
        var collectorDeadline = 2 * _options.PollIntervalSeconds + _options.TimeoutSeconds + 5;
        var staleAfter = result.Kind == "challenge_heartbeat" ? ChallengeHeartbeatStore.FreshSeconds
            : result.Kind is "service_heartbeat" or "cluster_heartbeat" ? 150 : collectorDeadline;
        var collectorStale = _updatedAt is { } updated && (_clock.GetUtcNow() - updated).TotalSeconds > collectorDeadline;
        return result.CheckedAt is { } at && (collectorStale || (_clock.GetUtcNow() - at).TotalSeconds > staleAfter)
            ? result with { Status = "STALE", ReasonCode = "MONITOR_REFRESH_STALE", Risk = RiskEvaluator.Unmeasured("STALE") }
            : result;
    }

    public IReadOnlyList<MonitorEvent> Events()
    {
        lock (_gate) return _events.Reverse().ToArray();
    }

    public IReadOnlyList<MonitorSample> History()
    {
        lock (_gate) return _history.ToArray();
    }

    public string Metrics()
    {
        var snapshot = Snapshot();
        var text = new StringBuilder();
        text.AppendLine("# HELP sentinel_target_health Target status, 1 healthy, 0 down, -1 unverified or unchecked");
        text.AppendLine("# TYPE sentinel_target_health gauge");
        text.AppendLine("# TYPE sentinel_target_latency_ms gauge");
        text.AppendLine("# TYPE sentinel_target_checked_at_seconds gauge");
        text.AppendLine("# HELP sentinel_target_risk_level 0 normal, 1 warning, 2 critical, -1 unknown or pending");
        text.AppendLine("# TYPE sentinel_target_risk_level gauge");
        foreach (var metric in new[] { "cpu_used_percent", "memory_used_percent", "disk_used_percent" })
            text.AppendLine($"# TYPE sentinel_host_{metric} gauge");
        foreach (var target in snapshot.Targets)
        {
            var labels = $"target_id=\"{target.TargetId}\"";
            var riskLevel = (target.Risk?.Level ?? RiskEvaluator.BaseLevel(target.Status)) switch
            { "NORMAL" => 0, "WARNING" => 1, "CRITICAL" => 2, _ => -1 };
            text.AppendLine($"sentinel_target_risk_level{{{labels}}} {riskLevel}");
            text.AppendLine($"sentinel_target_health{{{labels}}} {(target.Status == "HEALTHY" ? 1 : target.Status == "DOWN" ? 0 : -1)}");
            if ((target.Kind is "service_heartbeat" or "cluster_heartbeat") && target.Status is "HEALTHY" or "DOWN" or "DEGRADED")
                foreach (var metric in new[] { "cpu_used_percent", "memory_used_percent", "disk_used_percent" })
                    if (double.TryParse(target.Components.GetValueOrDefault(metric), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
                        && double.IsFinite(value) && value is >= 0 and <= 100)
                        text.AppendLine($"sentinel_host_{metric}{{{labels}}} {value.ToString(CultureInfo.InvariantCulture)}");
            if (target.LatencyMs is { } latency)
                text.AppendLine($"sentinel_target_latency_ms{{{labels}}} {latency.ToString(CultureInfo.InvariantCulture)}");
            if (target.CheckedAt is { } at)
                text.AppendLine($"sentinel_target_checked_at_seconds{{{labels}}} {at.ToUnixTimeSeconds()}");
        }
        text.AppendLine("# TYPE sentinel_event_storage_healthy gauge");
        text.AppendLine($"sentinel_event_storage_healthy {(snapshot.EventStorageHealthy ? 1 : 0)}");
        return text.ToString();
    }

    private void ObserveTransition(ProbeObservation result)
    {
        if (result.Status is "UNCONFIGURED" or "PENDING") return;
        var streak = _streaks.GetValueOrDefault(result.TargetId, (Failures: 0, Successes: 0, Reported: "UNKNOWN"));
        var healthy = result.Status == "HEALTHY";
        var failures = healthy ? 0 : streak.Failures + 1;
        var successes = healthy ? streak.Successes + 1 : 0;
        var reported = streak.Reported;
        var ready = healthy ? successes >= _options.RecoveryThreshold : failures >= _options.FailureThreshold;
        if (ready && reported != result.Status)
        {
            if (!(reported == "UNKNOWN" && healthy))
            {
                var incident = new MonitorEvent(Guid.NewGuid().ToString(), result.TargetId, result.Track,
                    reported, result.Status, result.ReasonCode, _clock.GetUtcNow());
                _events.Enqueue(incident);
                while (_events.Count > 200) _events.Dequeue();
                Persist(incident);
                _logger.LogWarning("Platform state changed. target_id={TargetId} status={Status} reason_code={ReasonCode}",
                    incident.TargetId, incident.Status, incident.ReasonCode);
            }
            reported = result.Status;
        }
        _streaks[result.TargetId] = (Math.Min(failures, 1000), Math.Min(successes, 1000), reported);
    }

    private void LoadEvents()
    {
        try
        {
            if (!File.Exists(_options.EventFile)) return;
            foreach (var line in File.ReadLines(_options.EventFile).TakeLast(200))
            {
                try
                {
                    var item = JsonSerializer.Deserialize<MonitorEvent>(line, PlatformJson.Options);
                    if (item is not null && !string.IsNullOrWhiteSpace(item.TargetId) &&
                        !string.IsNullOrWhiteSpace(item.Track) && Guid.TryParse(item.EventId, out _) &&
                        item.Status is "HEALTHY" or "DOWN" or "UNVERIFIED" or "STALE" &&
                        item.PreviousStatus is "UNKNOWN" or "HEALTHY" or "DOWN" or "UNVERIFIED" or "STALE" &&
                        !string.IsNullOrWhiteSpace(item.ReasonCode) && item.CreatedAt != default)
                    {
                        _events.Enqueue(item);
                        if (_observations.ContainsKey(item.TargetId))
                            _streaks[item.TargetId] = (0, 0, item.Status);
                    }
                    else _storageHealthy = false;
                }
                catch (JsonException) { _storageHealthy = false; }
            }
        }
        catch (IOException) { _storageHealthy = false; }
        catch (UnauthorizedAccessException) { _storageHealthy = false; }
    }

    private void Persist(MonitorEvent incident)
    {
        try
        {
            var parent = Path.GetDirectoryName(Path.GetFullPath(_options.EventFile));
            Directory.CreateDirectory(parent!);
            File.AppendAllText(_options.EventFile, JsonSerializer.Serialize(incident, PlatformJson.Options) + "\n");
            _storageHealthy = true;
        }
        catch (IOException) { _storageHealthy = false; }
        catch (UnauthorizedAccessException) { _storageHealthy = false; }
    }
}
