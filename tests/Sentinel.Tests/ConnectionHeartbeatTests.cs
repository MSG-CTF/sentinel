using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class ConnectionHeartbeatTests
{
    private static readonly DateTimeOffset At = DateTimeOffset.Parse("2026-11-03T06:00:00Z");
    private static PlatformTarget Target(bool connections = true) => new()
    {
        TargetId = "windows", Name = "Windows RDP", Role = "WINDOWS", Track = "RUNTIME", Kind = "service_heartbeat",
        AuthTokenEnv = "WINDOWS_HEARTBEAT_TOKEN", ExpectedFields = connections
            ? new() { ["probe_kind"] = "rdp_tcp", ["connection_target_count"] = "7" } : [],
    };
    private static ServiceHeartbeat Sample(int ready = 7) => new(At, ready == 7 ? "active" : "failed",
        null, null, null, null, "unverified", ConnectionTargetCount: 7, ConnectionReadyCount: ready);
    private static HeartbeatStore Store(PlatformTarget target, TimeProvider? clock = null)
        => new(Options.Create(new PlatformOptions { Targets = [target] }), clock ?? new Clock());
    private sealed class Clock : TimeProvider { internal DateTimeOffset At = ConnectionHeartbeatTests.At; public override DateTimeOffset GetUtcNow() => At; }

    [Fact]
    public void ExplicitConnectionBindingReportsReadinessWithoutInventingHostMetrics()
    {
        var target = Target(); Assert.True(target.IsValid()); var store = Store(target);
        Assert.True(store.Accept("windows", Sample()));
        var observation = store.Read(target);
        Assert.Equal("HEALTHY", observation.Status); Assert.Equal("RDP_CONNECTIONS_READY", observation.ReasonCode);
        Assert.Equal("7", observation.Components["connection_target_count"]); Assert.Equal("7", observation.Components["connection_ready_count"]);
        Assert.Equal("rdp_tcp", observation.Components["probe_kind"]);
        foreach (var key in new[] { "cpu_used_percent", "memory_used_percent", "disk_used_percent", "restart_count", "hostname", "os_name" })
            Assert.False(observation.Components.ContainsKey(key), key);
        Assert.Empty(new RiskEvaluator(SustainedRiskTests.Policy()).Evaluate(observation).Signals);
    }

    [Fact]
    public void UnreachableListenerAndStaleReportNeverRemainHealthy()
    {
        var target = Target(); var clock = new Clock(); var store = Store(target, clock);
        Assert.True(store.Accept("windows", Sample(6)));
        Assert.Equal("DOWN", store.Read(target).Status); Assert.Equal("RDP_CONNECTIONS_UNREACHABLE", store.Read(target).ReasonCode);
        clock.At = At.AddSeconds(151); Assert.Equal("STALE", store.Read(target).Status);
    }

    [Fact]
    public void ConnectionCountsMustMatchBindingAndStateAndCannotContainOtherHostResources()
    {
        var invalid = new[] { Sample() with { ConnectionTargetCount = 8 }, Sample() with { ConnectionReadyCount = 8 },
            Sample() with { ConnectionReadyCount = null }, Sample() with { ConnectionReadyCount = -1 },
            Sample(6) with { ServiceState = "active" }, Sample() with { GatewayState = "connected" },
            Sample() with { CpuUsedPercent = 0 }, Sample() with { MemoryUsedPercent = 0 }, Sample() with { RestartCount = 0 },
            Sample() with { Hostname = "linux-collector" } };
        foreach (var report in invalid) Assert.False(Store(Target()).Accept("windows", report));
    }

    [Fact]
    public void OrdinaryHostReportsStillRequireHostMetricsAndRejectConnectionCounters()
    {
        var store = Store(Target(false));
        Assert.False(store.Accept("windows", Sample() with { ConnectionTargetCount = null, ConnectionReadyCount = null }));
        var host = new ServiceHeartbeat(At, "active", 0, 40, 60, 0, "unverified");
        Assert.True(store.Accept("windows", host));
        Assert.False(Store(Target(false)).Accept("windows", host with { MemoryUsedPercent = null }));
        Assert.False(Store(Target(false)).Accept("windows", host with { DiskUsedPercent = double.NaN }));
        Assert.False(Store(Target(false)).Accept("windows", host with { ConnectionTargetCount = 7, ConnectionReadyCount = 7 }));
    }

    [Fact]
    public void ConnectionBindingRequiresARecognizedKindAndBoundedFleetSize()
    {
        foreach (var count in new[] { "0", "33", "unknown", "" })
        { var target = Target(); target.ExpectedFields["connection_target_count"] = count; Assert.False(target.IsValid()); }
        var wrong = Target(); wrong.ExpectedFields["probe_kind"] = "arbitrary_tcp"; Assert.False(wrong.IsValid());
    }

    [Fact]
    public void ReplayedReportCannotRefreshConnectionReadiness()
    {
        var store = Store(Target()); Assert.True(store.Accept("windows", Sample()));
        Assert.False(store.Accept("windows", Sample(0))); Assert.Equal("HEALTHY", store.Read(Target()).Status);
    }
}
