using System.Globalization;
using Microsoft.Extensions.Configuration;

namespace Sentinel.Monitor.Platform;

public sealed class RiskThreshold
{
    public double Warning { get; set; }
    public double Critical { get; set; }
    [ConfigurationKeyName("clear_margin")]
    public double ClearMargin { get; set; }
    public bool IsValid(double maximum) => double.IsFinite(Warning) && double.IsFinite(Critical)
        && double.IsFinite(ClearMargin) && Warning > 0 && Critical > Warning && Critical <= maximum
        && ClearMargin > 0 && ClearMargin < Warning && ClearMargin < Critical - Warning;
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
    double Warning, double Critical, double ClearBelow, bool Recovering);
public sealed record RiskAssessment(string Level, IReadOnlyList<RiskSignal> Signals);

// The collector owns hysteresis. Browser reloads and repeated heartbeat reads cannot clear a warning.
public sealed class RiskEvaluator(RiskPolicy policy)
{
    private sealed record State(int Level, int ClearCount, DateTimeOffset At);
    private readonly Dictionary<(string Target, string Metric), State> _states = [];
    public static string BaseLevel(string status) => status switch
    {
        "DOWN" => "CRITICAL", "DEGRADED" => "WARNING", "HEALTHY" => "NORMAL",
        "UNCONFIGURED" or "PENDING" => "PENDING", _ => "UNKNOWN",
    };
    public static RiskAssessment Unmeasured(string status) => new(BaseLevel(status), []);

    public RiskAssessment Evaluate(ProbeObservation observation)
    {
        if (!observation.Enabled || observation.Status is "UNCONFIGURED" or "PENDING" or "STALE" or "UNVERIFIED"
            || observation.CheckedAt is null)
        {
            foreach (var key in _states.Keys.Where(key => key.Target == observation.TargetId).ToArray()) _states.Remove(key);
            return Unmeasured(observation.Enabled ? observation.Status : "UNCONFIGURED");
        }
        var signals = new List<RiskSignal>();
        void Add(string metric, double? value, string unit, RiskThreshold threshold, double maximum)
        {
            var key = (observation.TargetId, metric);
            if (value is not { } number || !double.IsFinite(number) || number < 0 || number > maximum)
            { _states.Remove(key); return; }
            var at = observation.CheckedAt.Value;
            var previous = _states.GetValueOrDefault(key, new State(0, 0, DateTimeOffset.MinValue));
            var raw = number >= threshold.Critical ? 2 : number >= threshold.Warning ? 1 : 0;
            var level = previous.Level;
            var clearCount = previous.ClearCount;
            var clearBelow = (level == 2 ? threshold.Critical : threshold.Warning) - threshold.ClearMargin;
            if (at > previous.At)
            {
                if (raw >= level) { level = raw; clearCount = 0; }
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
                _states[key] = new State(level, clearCount, at);
            }
            signals.Add(new(metric, number, unit, level == 2 ? "CRITICAL" : level == 1 ? "WARNING" : "NORMAL",
                threshold.Warning, threshold.Critical,
                (level == 2 ? threshold.Critical : threshold.Warning) - threshold.ClearMargin, level > raw));
        }
        Add("latency_ms", observation.LatencyMs, "ms", policy.LatencyMs, double.MaxValue);
        if (observation.Kind == "service_heartbeat")
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
