using System.Globalization;
using Microsoft.Extensions.Configuration;

namespace Sentinel.Monitor.Platform;

public sealed class RiskThreshold
{
    public double Warning { get; set; }
    public double Critical { get; set; }
    [ConfigurationKeyName("clear_margin")]
    public double ClearMargin { get; set; }
    [ConfigurationKeyName("warning_seconds")]
    public int WarningSeconds { get; set; }
    [ConfigurationKeyName("warning_samples")]
    public int WarningSamples { get; set; } = 1;
    [ConfigurationKeyName("critical_seconds")]
    public int CriticalSeconds { get; set; }
    [ConfigurationKeyName("critical_samples")]
    public int CriticalSamples { get; set; } = 1;
    public bool IsValid(double maximum) => double.IsFinite(Warning) && double.IsFinite(Critical)
        && double.IsFinite(ClearMargin) && Warning > 0 && Critical > Warning && Critical <= maximum
        && ClearMargin > 0 && ClearMargin < Warning && ClearMargin < Critical - Warning
        && WarningSeconds is >= 0 and <= 1800 && CriticalSeconds is >= 0 and <= 1800
        && WarningSamples is >= 1 and <= 120 && CriticalSamples is >= 1 and <= 120;
}

public sealed class RiskPolicy
{
    [ConfigurationKeyName("cpu_used_percent")]
    public RiskThreshold CpuUsedPercent { get; set; } = new() { Warning = 80, Critical = 95, ClearMargin = 5 };
    [ConfigurationKeyName("latency_ms")]
    public RiskThreshold LatencyMs { get; set; } = new() { Warning = 1000, Critical = 3000, ClearMargin = 200 };
    [ConfigurationKeyName("memory_used_percent")]
    public RiskThreshold MemoryUsedPercent { get; set; } = new() { Warning = 80, Critical = 90, ClearMargin = 5 };
    [ConfigurationKeyName("disk_used_percent")]
    public RiskThreshold DiskUsedPercent { get; set; } = new() { Warning = 80, Critical = 90, ClearMargin = 5 };
    [ConfigurationKeyName("clear_samples")]
    public int ClearSamples { get; set; } = 2;
    public bool IsValid() => ClearSamples is >= 1 and <= 10 && LatencyMs.IsValid(30000)
        && MemoryUsedPercent.IsValid(100) && DiskUsedPercent.IsValid(100) && CpuUsedPercent.IsValid(100);
}

public sealed record RiskSignal(string Metric, double Value, string Unit, string Level,
    double Warning, double Critical, double ClearBelow, bool Recovering, string? PendingLevel = null);
public sealed record RiskAssessment(string Level, IReadOnlyList<RiskSignal> Signals);

// The collector owns entry confirmation and recovery hysteresis for every consumer.
public sealed class RiskEvaluator(RiskPolicy policy, int probeGapSeconds = 39)
{
    private sealed record Entry(DateTimeOffset Since, int Count);
    private sealed record State(int Level, int ClearCount, DateTimeOffset At,
        Entry? Warning, Entry? Critical, RiskSignal Signal);
    private readonly Dictionary<(string Target, string Metric), State> _states = [];
    public static string BaseLevel(string status) => status switch
    {
        "DOWN" => "CRITICAL", "DEGRADED" => "WARNING", "HEALTHY" => "NORMAL",
        "UNCONFIGURED" or "PENDING" => "PENDING", _ => "UNKNOWN",
    };
    public static RiskAssessment Unmeasured(string status) => new(BaseLevel(status), []);

    public RiskAssessment Evaluate(ProbeObservation observation)
    {
        if (!observation.Enabled || BaseLevel(observation.Status) is "UNKNOWN" or "PENDING"
            || observation.CheckedAt is null)
        {
            foreach (var key in _states.Keys.Where(key => key.Target == observation.TargetId).ToArray()) _states.Remove(key);
            return Unmeasured(observation.Enabled ? observation.Status : "UNCONFIGURED");
        }
        var signals = new List<RiskSignal>();
        void Add(string metric, double? value, string unit, RiskThreshold threshold, double maximum)
        {
            var key = (observation.TargetId, metric);
            var at = observation.CheckedAt.Value;
            var previous = _states.GetValueOrDefault(key);
            // Re-reading or receiving an older report must not change values or confirmation streaks.
            if (previous is not null && at <= previous.At) { signals.Add(previous.Signal); return; }
            if (value is not { } number || !double.IsFinite(number) || number < 0 || number > maximum)
            { _states.Remove(key); return; }
            var raw = number >= threshold.Critical ? 2 : number >= threshold.Warning ? 1 : 0;
            var level = previous?.Level ?? 0;
            var maximumGap = observation.Kind is "service_heartbeat" or "cluster_heartbeat" ? 150 : probeGapSeconds;
            var continuous = previous is not null && (at - previous.At).TotalSeconds <= maximumGap;
            var clearCount = continuous ? previous!.ClearCount : 0;
            Entry? Track(Entry? entry, bool breached, int required) => !breached ? null
                : entry is null ? new(at, 1) : entry with { Count = Math.Min(required, entry.Count + 1) };
            var warning = Track(continuous ? previous!.Warning : null, raw >= 1, threshold.WarningSamples);
            var critical = Track(continuous ? previous!.Critical : null, raw == 2, threshold.CriticalSamples);
            bool Confirmed(Entry? entry, int samples, int seconds) => entry is not null && entry.Count >= samples
                && (at - entry.Since).TotalSeconds >= seconds;
            var entered = Confirmed(critical, threshold.CriticalSamples, threshold.CriticalSeconds) ? 2
                : Confirmed(warning, threshold.WarningSamples, threshold.WarningSeconds) ? 1 : 0;
            var clearBelow = (level == 2 ? threshold.Critical : threshold.Warning) - threshold.ClearMargin;
            if (entered > level) { level = entered; clearCount = 0; }
            else if (raw >= level) clearCount = 0;
            else if (number < clearBelow)
            {
                clearCount++;
                if (clearCount >= policy.ClearSamples)
                {
                    // Clearing critical does not bypass the lower warning's clear margin.
                    level = raw == 0 && number >= threshold.Warning - threshold.ClearMargin ? 1 : raw;
                    clearCount = 0;
                }
            }
            else clearCount = 0;
            var signal = new RiskSignal(metric, number, unit, level == 2 ? "CRITICAL" : level == 1 ? "WARNING" : "NORMAL",
                threshold.Warning, threshold.Critical,
                (level == 2 ? threshold.Critical : threshold.Warning) - threshold.ClearMargin, level > raw,
                raw > level ? raw == 2 ? "CRITICAL" : "WARNING" : null);
            _states[key] = new(level, clearCount, at, warning, critical, signal);
            signals.Add(signal);
        }
        Add("latency_ms", observation.LatencyMs, "ms", policy.LatencyMs, double.MaxValue);
        if (observation.Kind is "service_heartbeat" or "cluster_heartbeat")
        {
            double? Component(string name) => double.TryParse(observation.Components.GetValueOrDefault(name),
                NumberStyles.Float, CultureInfo.InvariantCulture, out var value) ? value : null;
            Add("memory_used_percent", Component("memory_used_percent"), "%", policy.MemoryUsedPercent, 100);
            Add("cpu_used_percent", Component("cpu_used_percent"), "%", policy.CpuUsedPercent, 100);
            Add("disk_used_percent", Component("disk_used_percent"), "%", policy.DiskUsedPercent, 100);
        }
        var statusLevel = BaseLevel(observation.Status);
        var levelName = statusLevel == "CRITICAL" || signals.Any(signal => signal.Level == "CRITICAL") ? "CRITICAL"
            : statusLevel == "WARNING" || signals.Any(signal => signal.Level == "WARNING") ? "WARNING" : statusLevel;
        return new(levelName, signals);
    }
}
