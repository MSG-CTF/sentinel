using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Platform;

public sealed record ServiceHeartbeat(DateTimeOffset CheckedAt, string ServiceState, int RestartCount,
    double MemoryUsedPercent, double DiskUsedPercent, double LoadOneMinute, string GatewayState,
    double? CpuUsedPercent = null, int? CpuLogicalCount = null, string? CpuModel = null,
    string? OsName = null, string? Architecture = null, string? Hostname = null,
    long? MemoryTotalBytes = null, long? MemoryUsedBytes = null,
    long? DiskTotalBytes = null, long? DiskUsedBytes = null, long? UptimeSeconds = null);

public sealed class HeartbeatStore(IOptions<PlatformOptions> options, TimeProvider? clock = null)
{
    private readonly TimeProvider _clock = clock ?? TimeProvider.System;
    private readonly object _gate = new();
    private readonly Dictionary<string, ServiceHeartbeat> _latest = [];

    public bool Authorize(string id, string authorization)
    {
        var target = options.Value.Targets.Find(t => t.TargetId == id && t.Kind == "service_heartbeat" && t.Enabled);
        if (target?.AuthTokenEnv is null || !authorization.StartsWith("Bearer ", StringComparison.Ordinal)) return false;
        var token = Environment.GetEnvironmentVariable(target.AuthTokenEnv);
        return token?.Length >= 32 && CryptographicOperations.FixedTimeEquals(
            SHA256.HashData(Encoding.UTF8.GetBytes(token)), SHA256.HashData(Encoding.UTF8.GetBytes(authorization[7..])));
    }

    public bool Accept(string id, ServiceHeartbeat value)
    {
        if (options.Value.Targets.All(t => t.TargetId != id || t.Kind != "service_heartbeat" || !t.Enabled)
            || Math.Abs((_clock.GetUtcNow() - value.CheckedAt).TotalSeconds) > 90
            || value.ServiceState is not ("active" or "inactive" or "failed" or "activating" or "deactivating")
            || value.GatewayState is not ("unverified" or "connected" or "disconnected")
            || value.RestartCount is < 0 or > 1000000
            || !double.IsFinite(value.MemoryUsedPercent) || value.MemoryUsedPercent is < 0 or > 100
            || !double.IsFinite(value.DiskUsedPercent) || value.DiskUsedPercent is < 0 or > 100
            || !double.IsFinite(value.LoadOneMinute) || value.LoadOneMinute is < 0 or > 10000
            || value.CpuUsedPercent is { } cpu && (!double.IsFinite(cpu) || cpu is < 0 or > 100)
            || value.CpuLogicalCount is < 1 or > 65536
            || !SafeText(value.CpuModel, 160) || !SafeText(value.OsName, 160)
            || !SafeText(value.Architecture, 32) || !SafeText(value.Hostname, 128)
            || !Capacity(value.MemoryTotalBytes, value.MemoryUsedBytes)
            || !Capacity(value.DiskTotalBytes, value.DiskUsedBytes)
            || value.UptimeSeconds is < 0 or > 3153600000) return false;
        lock (_gate)
        {
            if (_latest.TryGetValue(id, out var old) && value.CheckedAt <= old.CheckedAt) return false;
            _latest[id] = value;
        }
        return true;
    }

    private static bool SafeText(string? value, int maximum) => value is null ||
        value.Length is > 0 && value.Length <= maximum && !value.Any(char.IsControl);
    private static bool Capacity(long? total, long? used) => total is null && used is null ||
        total is > 0 and <= 9007199254740991 && used is >= 0 && used <= total;

    public ProbeObservation Read(PlatformTarget target)
    {
        ServiceHeartbeat? value;
        lock (_gate) _latest.TryGetValue(target.TargetId, out value);
        if (value is null) return PlatformProbeClient.Result(target, "PENDING", "HEARTBEAT_PENDING", null, null, null);
        var stale = (_clock.GetUtcNow() - value.CheckedAt).TotalSeconds > 150;
        var healthy = value.ServiceState == "active";
        var components = new Dictionary<string, string>
        {
            ["service_state"] = value.ServiceState, ["gateway_state"] = value.GatewayState,
            ["restart_count"] = value.RestartCount.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["memory_used_percent"] = value.MemoryUsedPercent.ToString("F1", System.Globalization.CultureInfo.InvariantCulture),
            ["disk_used_percent"] = value.DiskUsedPercent.ToString("F1", System.Globalization.CultureInfo.InvariantCulture),
            ["load_one_minute"] = value.LoadOneMinute.ToString("F2", System.Globalization.CultureInfo.InvariantCulture),
        };
        void Add(string name, object? item) { if (item is not null) components[name] = Convert.ToString(item, System.Globalization.CultureInfo.InvariantCulture)!; }
        Add("cpu_used_percent", value.CpuUsedPercent); Add("cpu_logical_count", value.CpuLogicalCount);
        Add("cpu_model", value.CpuModel); Add("os_name", value.OsName); Add("architecture", value.Architecture);
        Add("hostname", value.Hostname); Add("memory_total_bytes", value.MemoryTotalBytes);
        Add("memory_used_bytes", value.MemoryUsedBytes); Add("disk_total_bytes", value.DiskTotalBytes);
        Add("disk_used_bytes", value.DiskUsedBytes); Add("uptime_seconds", value.UptimeSeconds);
        return PlatformProbeClient.Result(target, stale ? "STALE" : healthy ? "HEALTHY" : "DOWN",
            stale ? "HEARTBEAT_STALE" : healthy ? "SERVICE_ACTIVE" : "SERVICE_INACTIVE", value.CheckedAt, null, null, components);
    }
}
