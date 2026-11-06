using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Platform;

public sealed record ServiceHeartbeat(DateTimeOffset CheckedAt, string ServiceState, int? RestartCount,
    double? MemoryUsedPercent, double? DiskUsedPercent, double? LoadOneMinute, string GatewayState,
    double? CpuUsedPercent = null, int? CpuLogicalCount = null, string? CpuModel = null,
    string? OsName = null, string? Architecture = null, string? Hostname = null,
    long? MemoryTotalBytes = null, long? MemoryUsedBytes = null,
    long? DiskTotalBytes = null, long? DiskUsedBytes = null, long? UptimeSeconds = null,
    int? ConnectionTargetCount = null, int? ConnectionReadyCount = null);

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
        var target = options.Value.Targets.Find(t => t.TargetId == id && t.Kind == "service_heartbeat" && t.Enabled);
        var connections = target?.ExpectedFields.GetValueOrDefault("probe_kind") == "rdp_tcp";
        if (target is null
            || target.ExpectedFields.TryGetValue("hostname", out var hostname)
                && !string.Equals(hostname, value.Hostname, StringComparison.Ordinal)
            || Math.Abs((_clock.GetUtcNow() - value.CheckedAt).TotalSeconds) > 90
            || value.ServiceState is not ("active" or "inactive" or "failed" or "activating" or "deactivating")
            || value.GatewayState is not ("unverified" or "connected" or "disconnected")
            || value.RestartCount is < 0 or > 1000000
            || !Metric(value.MemoryUsedPercent, 100, !connections)
            || !Metric(value.DiskUsedPercent, 100, !connections)
            || !Metric(value.LoadOneMinute, 10000, !connections)
            || value.CpuUsedPercent is { } cpu && (!double.IsFinite(cpu) || cpu is < 0 or > 100)
            || value.CpuLogicalCount is < 1 or > 65536
            || !SafeText(value.CpuModel, 160) || !SafeText(value.OsName, 160)
            || !SafeText(value.Architecture, 32) || !SafeText(value.Hostname, 128)
            || !Capacity(value.MemoryTotalBytes, value.MemoryUsedBytes)
            || !Capacity(value.DiskTotalBytes, value.DiskUsedBytes)
            || value.UptimeSeconds is < 0 or > 3153600000) return false;
        if (connections)
        {
            if (!int.TryParse(target.ExpectedFields.GetValueOrDefault("connection_target_count"), out var expected)
                || expected is < 1 or > 32 || value.ConnectionTargetCount != expected
                || value.ConnectionReadyCount is not { } ready || ready < 0 || ready > expected
                || value.ServiceState != (ready == expected ? "active" : "failed")
                || value.GatewayState != "unverified"
                || value.RestartCount is not null || value.MemoryUsedPercent is not null || value.DiskUsedPercent is not null
                || value.LoadOneMinute is not null || value.CpuUsedPercent is not null || value.CpuLogicalCount is not null
                || value.CpuModel is not null || value.OsName is not null || value.Architecture is not null || value.Hostname is not null
                || value.MemoryTotalBytes is not null || value.MemoryUsedBytes is not null
                || value.DiskTotalBytes is not null || value.DiskUsedBytes is not null || value.UptimeSeconds is not null) return false;
        }
        else if (value.RestartCount is null || value.ConnectionTargetCount is not null || value.ConnectionReadyCount is not null) return false;
        lock (_gate)
        {
            if (_latest.TryGetValue(id, out var old) && value.CheckedAt <= old.CheckedAt) return false;
            _latest[id] = value;
        }
        return true;
    }

    private static bool Metric(double? value, double maximum, bool required) => value is { } number
        ? double.IsFinite(number) && number >= 0 && number <= maximum : !required;

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
        };
        void Add(string name, object? item) { if (item is not null) components[name] = Convert.ToString(item, System.Globalization.CultureInfo.InvariantCulture)!; }
        Add("restart_count", value.RestartCount);
        Add("memory_used_percent", value.MemoryUsedPercent?.ToString("F1", System.Globalization.CultureInfo.InvariantCulture));
        Add("disk_used_percent", value.DiskUsedPercent?.ToString("F1", System.Globalization.CultureInfo.InvariantCulture));
        Add("load_one_minute", value.LoadOneMinute?.ToString("F2", System.Globalization.CultureInfo.InvariantCulture));
        var connections = target.ExpectedFields.GetValueOrDefault("probe_kind") == "rdp_tcp";
        if (connections)
        {
            components["probe_kind"] = "rdp_tcp";
            Add("connection_target_count", value.ConnectionTargetCount); Add("connection_ready_count", value.ConnectionReadyCount);
        }
        Add("cpu_used_percent", value.CpuUsedPercent); Add("cpu_logical_count", value.CpuLogicalCount);
        Add("cpu_model", value.CpuModel); Add("os_name", value.OsName); Add("architecture", value.Architecture);
        Add("hostname", value.Hostname); Add("memory_total_bytes", value.MemoryTotalBytes);
        Add("memory_used_bytes", value.MemoryUsedBytes); Add("disk_total_bytes", value.DiskTotalBytes);
        Add("disk_used_bytes", value.DiskUsedBytes); Add("uptime_seconds", value.UptimeSeconds);
        return PlatformProbeClient.Result(target, stale ? "STALE" : healthy ? "HEALTHY" : "DOWN",
            stale ? "HEARTBEAT_STALE" : connections ? healthy ? "RDP_CONNECTIONS_READY" : "RDP_CONNECTIONS_UNREACHABLE"
                : healthy ? "SERVICE_ACTIVE" : "SERVICE_INACTIVE", value.CheckedAt, null, null, components);
    }
}
