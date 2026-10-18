using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class ClusterHeartbeatTests
{
    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now = new(2026, 10, 18, 14, 0, 0, TimeSpan.Zero);
        public override DateTimeOffset GetUtcNow() => Now;
    }
    private static PlatformTarget Target(string id = "cluster-one", string cluster = "cluster-a") => new()
    {
        TargetId = id, Name = id, Role = "K3S", Track = "RUNTIME", Kind = "cluster_heartbeat",
        AuthTokenEnv = "SENTINEL_TEST_CLUSTER_TOKEN", ExpectedFields = new() { ["cluster_target_id"] = cluster },
    };
    private static ClusterHeartbeat Sample(Clock clock) => new(clock.Now, "cluster-a", "ok", 1, 1, 1, 0, 0, 0, 0,
        CpuUsedPercent: 10, MemoryUsedPercent: 25, CpuLogicalCount: 2, MemoryTotalBytes: 1000, MemoryUsedBytes: 250,
        OsName: "Debian 12", Architecture: "amd64", Hostname: "node-one");

    [Fact]
    public void RequiresAConfiguredIdentityAndDedicatedBearerToken()
    {
        var target = Target();
        var store = new ClusterHeartbeatStore(Options.Create(new PlatformOptions { Targets = [target] }));
        var token = new string('x', 48);
        Environment.SetEnvironmentVariable(target.AuthTokenEnv!, token);
        try
        {
            Assert.True(target.IsValid());
            Assert.True(store.Authorize(target.TargetId, "Bearer " + token));
            Assert.False(store.Authorize("other-cluster", "Bearer " + token));
            Assert.False(store.Authorize(target.TargetId, "Bearer " + token + ", other"));
            Assert.False(store.Authorize(target.TargetId, "Basic " + token));
            var unbound = Target(); unbound.ExpectedFields.Clear();
            Assert.False(unbound.IsValid());
        }
        finally { Environment.SetEnvironmentVariable(target.AuthTokenEnv!, null); }
    }

    [Fact]
    public void RejectsIdentityConfusionReplayFutureAndInvalidResourceCounts()
    {
        var clock = new Clock(); var target = Target();
        var store = new ClusterHeartbeatStore(Options.Create(new PlatformOptions { Targets = [target] }), clock);
        var valid = Sample(clock);
        foreach (var sample in new[] {
            valid with { ClusterTargetId = "another-cluster" }, valid with { CheckedAt = clock.Now.AddSeconds(31) },
            valid with { CheckedAt = clock.Now.AddSeconds(-91) }, valid with { ReadyNodeCount = 2 },
            valid with { SchedulableNodeCount = 2 }, valid with { CpuUsedPercent = double.NaN },
            valid with { MemoryUsedBytes = 1001 }, valid with { MemoryUsedBytes = null },
            valid with { Hostname = "node\nspoof" }, valid with { QueryState = "query_failed" },
        }) Assert.False(store.Accept(target.TargetId, sample));
        Assert.True(store.Accept(target.TargetId, valid));
        Assert.False(store.Accept(target.TargetId, valid));
        Assert.Equal("HEALTHY", store.Read(target).Status);
    }

    [Theory]
    [InlineData("not_ready", "DOWN", "K3S_NODE_NOT_READY")]
    [InlineData("pressure", "DOWN", "K3S_NODE_PRESSURE")]
    [InlineData("cordoned", "DEGRADED", "K3S_NODE_UNSCHEDULABLE")]
    [InlineData("unknown", "UNVERIFIED", "K3S_CONDITIONS_UNVERIFIED")]
    [InlineData("query_failed", "UNVERIFIED", "K3S_QUERY_UNVERIFIED")]
    [InlineData("not_registered", "UNVERIFIED", "K3S_QUERY_UNVERIFIED")]
    [InlineData("empty", "DOWN", "K3S_TOPOLOGY_INVALID")]
    public void UnhealthyAndUnobservedNodesCannotBecomeHealthy(string scenario, string status, string reason)
    {
        var clock = new Clock(); var target = Target();
        var store = new ClusterHeartbeatStore(Options.Create(new PlatformOptions { Targets = [target] }), clock);
        var value = new ClusterHeartbeat(clock.Now, "cluster-a", "ok", 1, 1, 1, 0, 0, 0, 0);
        value = scenario switch
        {
            "not_ready" => value with { ReadyNodeCount = 0, SchedulableNodeCount = 0 },
            "pressure" => value with { DiskPressureNodeCount = 1 },
            "cordoned" => value with { SchedulableNodeCount = 0 },
            "unknown" => value with { UnknownConditionNodeCount = 1 },
            "empty" => value with { NodeCount = 0, ReadyNodeCount = 0, SchedulableNodeCount = 0 },
            _ => value with { QueryState = scenario, NodeCount = 0, ReadyNodeCount = 0, SchedulableNodeCount = 0 },
        };
        Assert.True(store.Accept(target.TargetId, value));
        var observation = store.Read(target);
        Assert.Equal(status, observation.Status); Assert.Equal(reason, observation.ReasonCode);
        Assert.DoesNotContain("cpu_used_percent", observation.Components.Keys);
        Assert.DoesNotContain("disk_used_percent", observation.Components.Keys);
        clock.Now += TimeSpan.FromSeconds(151);
        Assert.Equal("STALE", store.Read(target).Status);
    }

    [Fact]
    public async Task MinuteReportsRetainTheirFreshnessAndNeedDistinctResourceRecoverySamples()
    {
        var clock = new Clock(); var target = Target();
        var file = Path.Combine(Path.GetTempPath(), "cluster-tests-" + Guid.NewGuid() + ".jsonl");
        var options = Options.Create(new PlatformOptions { Targets = [target], EventFile = file });
        var store = new ClusterHeartbeatStore(options, clock);
        var monitor = new PlatformMonitor(options, new PlatformProbeClient(new HttpClient(), clusterHeartbeatStore: store), NullLogger<PlatformMonitor>.Instance, clock);
        try
        {
            Assert.True(store.Accept(target.TargetId, Sample(clock) with { CpuUsedPercent = 98 }));
            await monitor.RefreshAsync();
            Assert.Equal("CRITICAL", monitor.Snapshot().Targets[0].Risk!.Level);
            clock.Now += TimeSpan.FromSeconds(65); await monitor.RefreshAsync();
            Assert.Equal("HEALTHY", monitor.Snapshot().Targets[0].Status);
            Assert.Contains("sentinel_host_cpu_used_percent{target_id=\"cluster-one\"} 98", monitor.Metrics());
            Assert.True(store.Accept(target.TargetId, Sample(clock)));
            await monitor.RefreshAsync(); await monitor.RefreshAsync();
            Assert.Equal("CRITICAL", monitor.Snapshot().Targets[0].Risk!.Level);
            clock.Now += TimeSpan.FromSeconds(60);
            Assert.True(store.Accept(target.TargetId, Sample(clock))); await monitor.RefreshAsync();
            Assert.Equal("NORMAL", monitor.Snapshot().Targets[0].Risk!.Level);
            clock.Now += TimeSpan.FromSeconds(151); await monitor.RefreshAsync();
            Assert.Equal("STALE", monitor.Snapshot().Targets[0].Status);
            Assert.DoesNotContain("sentinel_host_cpu_used_percent{", monitor.Metrics());
        }
        finally { File.Delete(file); }
    }
}
