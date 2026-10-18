using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Platform;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record ClusterHeartbeat(
    [property: JsonRequired] DateTimeOffset CheckedAt, [property: JsonRequired] string ClusterTargetId,
    [property: JsonRequired] string QueryState, [property: JsonRequired] int NodeCount,
    [property: JsonRequired] int ReadyNodeCount, [property: JsonRequired] int SchedulableNodeCount,
    [property: JsonRequired] int MemoryPressureNodeCount, [property: JsonRequired] int DiskPressureNodeCount,
    [property: JsonRequired] int PidPressureNodeCount, [property: JsonRequired] int UnknownConditionNodeCount,
    double? CpuUsedPercent = null, double? MemoryUsedPercent = null,
    int? CpuLogicalCount = null, long? MemoryTotalBytes = null, long? MemoryUsedBytes = null,
    string? OsName = null, string? Architecture = null, string? Hostname = null);

public sealed class ClusterHeartbeatStore(IOptions<PlatformOptions> options, TimeProvider? clock = null)
{
    private readonly TimeProvider _clock = clock ?? TimeProvider.System;
    private readonly object _gate = new();
    private readonly Dictionary<string, ClusterHeartbeat> _latest = [];

    public bool Authorize(string id, string authorization)
    {
        var target = Target(id);
        if (target?.AuthTokenEnv is null || !authorization.StartsWith("Bearer ", StringComparison.Ordinal)) return false;
        var token = Environment.GetEnvironmentVariable(target.AuthTokenEnv);
        return token?.Length >= 32 && CryptographicOperations.FixedTimeEquals(
            SHA256.HashData(Encoding.UTF8.GetBytes(token)), SHA256.HashData(Encoding.UTF8.GetBytes(authorization[7..])));
    }

    private PlatformTarget? Target(string id) => options.Value.Targets.Find(t =>
        t.TargetId == id && t.Kind == "cluster_heartbeat" && t.Enabled);

    public bool Accept(string id, ClusterHeartbeat value)
    {
        var target = Target(id);
        var counts = new[] { value.ReadyNodeCount, value.SchedulableNodeCount, value.MemoryPressureNodeCount,
            value.DiskPressureNodeCount, value.PidPressureNodeCount, value.UnknownConditionNodeCount };
        if (target is null || value.ClusterTargetId != target.ExpectedFields.GetValueOrDefault("cluster_target_id")
            || value.QueryState is not ("ok" or "query_failed" or "not_registered" or "disabled")
            || value.CheckedAt > _clock.GetUtcNow().AddSeconds(30) || _clock.GetUtcNow() - value.CheckedAt > TimeSpan.FromSeconds(90)
            || value.NodeCount is < 0 or > 100 || counts.Any(count => count < 0 || count > value.NodeCount)
            || value.SchedulableNodeCount > value.ReadyNodeCount
            || !Percentage(value.CpuUsedPercent) || !Percentage(value.MemoryUsedPercent)
            || value.CpuLogicalCount is < 1 or > 65536
            || !SafeText(value.OsName, 160) || !SafeText(value.Architecture, 32) || !SafeText(value.Hostname, 128)
            || value.MemoryTotalBytes is <= 0 or > 9007199254740991 || value.MemoryUsedBytes is < 0
            || value.MemoryUsedBytes is { } used && (value.MemoryTotalBytes is null || used > value.MemoryTotalBytes)
            || (value.MemoryUsedBytes is null) != (value.MemoryUsedPercent is null)
            || (value.QueryState != "ok" || value.NodeCount != 1) &&
                (value.CpuUsedPercent is not null || value.MemoryUsedPercent is not null || value.CpuLogicalCount is not null
                 || value.MemoryTotalBytes is not null || value.OsName is not null || value.Architecture is not null || value.Hostname is not null)
            || value.QueryState != "ok" && value.NodeCount != 0) return false;
        lock (_gate)
        {
            if (_latest.TryGetValue(id, out var previous) && value.CheckedAt <= previous.CheckedAt) return false;
            _latest[id] = value;
        }
        return true;
    }

    private static bool Percentage(double? value) => value is null || double.IsFinite(value.Value) && value is >= 0 and <= 100;
    private static bool SafeText(string? value, int maximum) => value is null ||
        value.Length is > 0 && value.Length <= maximum && !value.Any(char.IsControl);

    public ProbeObservation Read(PlatformTarget target)
    {
        ClusterHeartbeat? value;
        lock (_gate) _latest.TryGetValue(target.TargetId, out value);
        if (value is null) return PlatformProbeClient.Result(target, "PENDING", "HEARTBEAT_PENDING", null, null, null);
        var components = new Dictionary<string, string> { ["cluster_target_id"] = value.ClusterTargetId, ["query_state"] = value.QueryState };
        void Add(string name, object? item) { if (item is not null) components[name] = Convert.ToString(item, CultureInfo.InvariantCulture)!; }
        Add("node_count", value.NodeCount); Add("ready_node_count", value.ReadyNodeCount);
        Add("schedulable_node_count", value.SchedulableNodeCount); Add("memory_pressure_node_count", value.MemoryPressureNodeCount);
        Add("disk_pressure_node_count", value.DiskPressureNodeCount); Add("pid_pressure_node_count", value.PidPressureNodeCount);
        Add("unknown_condition_node_count", value.UnknownConditionNodeCount);
        Add("cpu_used_percent", value.CpuUsedPercent); Add("memory_used_percent", value.MemoryUsedPercent);
        Add("cpu_logical_count", value.CpuLogicalCount); Add("memory_total_bytes", value.MemoryTotalBytes);
        Add("memory_used_bytes", value.MemoryUsedBytes); Add("os_name", value.OsName);
        Add("architecture", value.Architecture); Add("hostname", value.Hostname);
        var (status, reason) = (_clock.GetUtcNow() - value.CheckedAt).TotalSeconds > 150 ? ("STALE", "HEARTBEAT_STALE")
            : value.QueryState != "ok" ? ("UNVERIFIED", "K3S_QUERY_UNVERIFIED")
            : value.NodeCount != 1 ? ("DOWN", "K3S_TOPOLOGY_INVALID")
            : value.UnknownConditionNodeCount > 0 ? ("UNVERIFIED", "K3S_CONDITIONS_UNVERIFIED")
            : value.ReadyNodeCount != 1 ? ("DOWN", "K3S_NODE_NOT_READY")
            : value.MemoryPressureNodeCount + value.DiskPressureNodeCount + value.PidPressureNodeCount > 0 ? ("DOWN", "K3S_NODE_PRESSURE")
            : value.SchedulableNodeCount != 1 ? ("DEGRADED", "K3S_NODE_UNSCHEDULABLE") : ("HEALTHY", "K3S_NODE_READY");
        return PlatformProbeClient.Result(target, status, reason, value.CheckedAt, null, null, components);
    }
}
