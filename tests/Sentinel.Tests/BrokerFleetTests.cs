using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using System.Text.Json;
using Xunit;

namespace Sentinel.Tests;

public sealed class BrokerFleetTests
{
    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
        public void Advance(int seconds) => Now = Now.AddSeconds(seconds);
    }
    private sealed class Fixture : IDisposable
    {
        public readonly string Directory = Path.Combine(Path.GetTempPath(), "sentinel-fleet-" + Guid.NewGuid());
        public readonly Clock Clock = new();
        public readonly BrokerFleetOptions Options;
        public readonly BrokerFleetStore Store;
        public Fixture()
        {
            System.IO.Directory.CreateDirectory(Directory);
            Options = new() { Enabled = true, StateFile = Path.Combine(Directory, "state.json") };
            Store = new(Microsoft.Extensions.Options.Options.Create(Options), Clock);
        }
        public void Dispose() => System.IO.Directory.Delete(Directory, true);
    }
    private static FleetNode Node() => new(Guid.NewGuid(), Guid.NewGuid(), "node-one", "test-project", "https://10.10.1.2:6443");
    private static FleetWindows Windows() => new(Guid.NewGuid(), "windows-one", "10.10.2.3", 3389);
    private static FleetRoster Roster(Fixture f, FleetNode[]? nodes = null, FleetWindows[]? windows = null) => new(1, f.Clock.Now, true, nodes ?? [Node()], windows ?? [Windows()]);
    private static FleetBatch Batch(Fixture f, FleetRoster roster) => new(1, f.Store.Version!, f.Clock.Now,
        roster.Nodes.Select(n => new FleetReport(n.ResourceTargetId, new ClusterHeartbeat(f.Clock.Now,
            n.NodeUid?.ToString() ?? "unbound-" + n.ResourceTargetId.ToString("N"), n.NodeUid is null ? "not_registered" : "ok",
            n.NodeUid is null ? 0 : 1, n.NodeUid is null ? 0 : 1, n.NodeUid is null ? 0 : 1, 0, 0, 0, 0)))
        .Concat(roster.Windows.Select(w => new FleetReport(w.ResourceTargetId, Windows: new(f.Clock.Now, true)))).ToArray());

    [Fact]
    public void FullSnapshotsAddRemoveDisableAndReuseStableIdsWithoutResettingUnchangedHealth()
    {
        using var f = new Fixture(); var first = Roster(f);
        Assert.Equal("ACCEPTED", f.Store.AcceptRoster(first));
        Assert.Equal("ACCEPTED", f.Store.AcceptBatch(Batch(f, first)));
        var before = f.Store.Observations(); Assert.All(before, row => Assert.Equal("HEALTHY", row.Status));
        var added = Node(); f.Clock.Advance(5);
        var next = first with { CheckedAt = f.Clock.Now, Nodes = [..first.Nodes, added] };
        Assert.Equal("ACCEPTED", f.Store.AcceptRoster(next));
        Assert.Equal(3, f.Store.Targets().Length);
        Assert.Equal("PENDING", f.Store.Observations().Single(o => o.TargetId == BrokerFleetStore.NodeId(added.ResourceTargetId)).Status);
        Assert.All(f.Store.Observations().Where(o => before.Any(b => b.TargetId == o.TargetId)), row => Assert.Equal("HEALTHY", row.Status));
        f.Clock.Advance(5); var removed = next with { CheckedAt = f.Clock.Now, Nodes = [added], Windows = [] };
        Assert.Equal("ACCEPTED", f.Store.AcceptRoster(removed));
        Assert.Single(f.Store.Targets());
        Assert.Equal("ROSTER_CHANGED", f.Store.AcceptBatch(Batch(f, first) with { RosterVersion = "old" }));
        f.Clock.Advance(5); Assert.Equal("ACCEPTED", f.Store.AcceptRoster(removed with { CheckedAt = f.Clock.Now, Nodes = [], Windows = [] }));
        Assert.Empty(f.Store.Targets());
    }

    [Fact]
    public void MetadataRefreshNeverRefreshesMeasurementsAndMissingPublisherRetainsUnknownMembers()
    {
        using var f = new Fixture(); var first = Roster(f);
        Assert.Equal("ACCEPTED", f.Store.AcceptRoster(first));
        Assert.Equal("ACCEPTED", f.Store.AcceptBatch(Batch(f, first)));
        var at = f.Store.Observations()[0].CheckedAt;
        f.Clock.Advance(80); Assert.Equal("ACCEPTED", f.Store.AcceptRoster(first with { CheckedAt = f.Clock.Now }));
        Assert.Equal(at, f.Store.Observations()[0].CheckedAt);
        f.Clock.Advance(80); Assert.All(f.Store.Observations(), row => Assert.Equal("STALE", row.Status));
        f.Clock.Advance(101); Assert.Equal(2, f.Store.Targets().Length);
        Assert.All(f.Store.Observations(), row => Assert.Equal("FLEET_ROSTER_STALE", row.ReasonCode));
        Assert.All(f.Store.Observations(), row => Assert.Equal("UNKNOWN", row.Risk!.Level));
    }

    [Fact]
    public void PartialReplayedMalformedAndOversizedRostersCannotDeleteCurrentMembers()
    {
        using var f = new Fixture(); var first = Roster(f); Assert.Equal("ACCEPTED", f.Store.AcceptRoster(first));
        var version = f.Store.Version; f.Clock.Advance(1);
        foreach (var invalid in new[] { first with { Complete = false }, first with { SchemaVersion = 2 },
            first with { Nodes = [first.Nodes[0],first.Nodes[0]] }, first with { Nodes = [null!] },
            first with { Nodes = Enumerable.Range(0,65).Select(_=>Node()).ToArray() },
            first with { Windows = [first.Windows[0] with { ResourceTargetId = first.Nodes[0].ResourceTargetId }] },
            first with { Nodes = [first.Nodes[0] with { ServerUrl = "https://169.254.169.254:6443" }] },
            first with { Windows = [first.Windows[0] with { Host = "127.0.0.1" }] } })
        {
            Assert.Equal("INVALID", f.Store.AcceptRoster(invalid with { CheckedAt = f.Clock.Now }));
            Assert.Equal(version, f.Store.Version); Assert.Equal(2,f.Store.Targets().Length);
        }
        Assert.Equal("REPLAY",f.Store.AcceptRoster(first));
    }

    [Fact]
    public void ChangedNodeIdentityAndBadBatchCannotReuseOrPartiallyReplaceMeasurements()
    {
        using var f = new Fixture(); var first = Roster(f); f.Store.AcceptRoster(first);
        var good = Batch(f,first);Assert.Equal("ACCEPTED",f.Store.AcceptBatch(good));
        f.Clock.Advance(1);var next=Batch(f,first);
        var bad=next with { Reports=[next.Reports[0],next.Reports[1] with { Windows=null }] };
        Assert.NotEqual("ACCEPTED",f.Store.AcceptBatch(bad));
        Assert.All(f.Store.Observations(),row=>Assert.Equal(good.CheckedAt,row.CheckedAt));
        Assert.Equal("REPLAY",f.Store.AcceptBatch(good));
        var rebuilt=first with { CheckedAt=f.Clock.Now,Nodes=[first.Nodes[0] with { NodeUid=Guid.NewGuid() }] };
        Assert.Equal("ACCEPTED",f.Store.AcceptRoster(rebuilt));
        Assert.Equal("PENDING",f.Store.Observations().Single(r=>r.Kind=="cluster_heartbeat").Status);
        Assert.Equal("PENDING",f.Store.Project(f.Store.Targets().Single(r=>r.Kind=="cluster_heartbeat"),
            new("other","old","K3S","RUNTIME","cluster_heartbeat",null,true,"HEALTHY","K3S_NODE_READY",f.Clock.Now,null,null,new Dictionary<string,string>())).Status);
        Assert.Equal("ROSTER_CHANGED",f.Store.AcceptBatch(next));
    }

    [Fact]
    public void RestartRestoresRosterButRequiresNewMeasurementsAndUnreadableStorageCannotAcceptChanges()
    {
        using var f=new Fixture();var first=Roster(f);f.Store.AcceptRoster(first);f.Store.AcceptBatch(Batch(f,first));
        var restored=new BrokerFleetStore(Microsoft.Extensions.Options.Options.Create(f.Options),f.Clock);
        Assert.Equal(f.Store.Version,restored.Version);Assert.Equal(2,restored.Targets().Length);
        Assert.All(restored.Observations(),r=>Assert.Equal("PENDING",r.Status));
        f.Clock.Advance(1);File.Delete(f.Options.StateFile);System.IO.Directory.CreateDirectory(f.Options.StateFile);
        Assert.Equal("STORAGE_UNAVAILABLE",f.Store.AcceptRoster(first with {CheckedAt=f.Clock.Now,Nodes=[]}));
        Assert.Equal(2,f.Store.Targets().Length);
        Assert.All(f.Store.Observations(),r=>Assert.Equal("UNKNOWN",r.Risk!.Level));
    }

    [Fact]
    public void MissingNodeBindingRemainsVisibleAndPublisherCannotUseObserverOrAdminScope()
    {
        using var f=new Fixture();var roster=Roster(f,nodes:[Node() with {NodeUid=null,ServerUrl=null}]);
        Assert.Equal("ACCEPTED",f.Store.AcceptRoster(roster));Assert.Equal("ACCEPTED",f.Store.AcceptBatch(Batch(f,roster)));
        Assert.Equal("UNVERIFIED",f.Store.Observations().Single(o=>o.Kind=="cluster_heartbeat").Status);
        var pub="FLEET_TEST_PUB_"+Guid.NewGuid().ToString("N");var observer="FLEET_TEST_OBS_"+Guid.NewGuid().ToString("N");
        f.Options.PublisherTokenEnv=pub;f.Options.ObserverTokenEnv=observer;
        try
        {
            Environment.SetEnvironmentVariable(pub,new string('p',48));Environment.SetEnvironmentVariable(observer,new string('o',48));
            Assert.True(f.Store.Authorize("Bearer "+new string('p',48),true));Assert.False(f.Store.Authorize("Bearer "+new string('p',48),false));
            Assert.False(f.Store.Authorize("Bearer "+new string('o',48),true));Assert.True(f.Store.Authorize("Bearer "+new string('o',48),false));
            Environment.SetEnvironmentVariable(observer,new string('p',48));Assert.False(f.Store.Authorize("Bearer "+new string('p',48),true));
        }
        finally {Environment.SetEnvironmentVariable(pub,null);Environment.SetEnvironmentVariable(observer,null);}
    }

    [Fact]
    public async Task MonitorAndMetricsFollowCatalogChangesWithoutRestartingOrLeavingOldAssets()
    {
        using var f=new Fixture();var profile=Microsoft.Extensions.Options.Options.Create(new PlatformOptions {Targets=[],EventFile=Path.Combine(f.Directory,"events.jsonl")});
        var probe=new PlatformProbeClient(new HttpClient());var monitor=new PlatformMonitor(profile,probe,NullLogger<PlatformMonitor>.Instance,f.Clock,f.Store);
        var first=Roster(f);f.Store.AcceptRoster(first);f.Store.AcceptBatch(Batch(f,first));
        await monitor.RefreshAsync();Assert.Equal(2,monitor.Snapshot().Targets.Count);
        Assert.Contains(BrokerFleetStore.NodeId(first.Nodes[0].ResourceTargetId),monitor.Metrics());
        f.Clock.Advance(1);f.Store.AcceptRoster(first with {CheckedAt=f.Clock.Now,Nodes=[]});
        Assert.Single(monitor.Snapshot().Targets);await monitor.RefreshAsync();Assert.Single(monitor.Snapshot().Targets);
        Assert.DoesNotContain(BrokerFleetStore.NodeId(first.Nodes[0].ResourceTargetId),monitor.Metrics());
    }
}
