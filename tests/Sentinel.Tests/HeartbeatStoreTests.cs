using Microsoft.Extensions.Options;
using Microsoft.Extensions.Logging.Abstractions;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class HeartbeatStoreTests
{
    [Fact]
    public async Task FasterHttpPollingDoesNotExpireMinuteReportsOrDuplicateRiskRecovery()
    {
        var clock = new Clock();
        var target = new PlatformTarget { TargetId="discord-bot",Name="Bot",Role="DISCORD",Track="CONTROL",
            Kind="service_heartbeat",AuthTokenEnv="SENTINEL_TEST_HEARTBEAT_RISK" };
        var file = Path.Combine(Path.GetTempPath(),"sentinel-risk-"+Guid.NewGuid()+".jsonl");
        var options = Options.Create(new PlatformOptions { PollIntervalSeconds=15,Targets=[target],EventFile=file });
        var store = new HeartbeatStore(options,clock);
        var monitor = new PlatformMonitor(options,new PlatformProbeClient(new HttpClient(),store),NullLogger<PlatformMonitor>.Instance,clock);
        try
        {
            Assert.True(store.Accept(target.TargetId,new(clock.Now,"active",0,92,40,.1,"unverified")));
            await monitor.RefreshAsync();
            Assert.Equal("CRITICAL",monitor.Snapshot().Targets[0].Risk!.Level);
            clock.Now += TimeSpan.FromSeconds(65);
            Assert.Equal("UNKNOWN",monitor.Snapshot().Targets[0].Risk!.Level);
            await monitor.RefreshAsync();
            Assert.Equal("HEALTHY",monitor.Snapshot().Targets[0].Status);
            Assert.Equal("CRITICAL",monitor.Snapshot().Targets[0].Risk!.Level);
            Assert.True(store.Accept(target.TargetId,new(clock.Now,"active",0,60,40,.1,"unverified")));
            await monitor.RefreshAsync(); await monitor.RefreshAsync();
            Assert.Equal("CRITICAL",monitor.Snapshot().Targets[0].Risk!.Level);
            clock.Now += TimeSpan.FromSeconds(60);
            Assert.True(store.Accept(target.TargetId,new(clock.Now,"active",0,60,40,.1,"unverified")));
            await monitor.RefreshAsync();
            Assert.Equal("NORMAL",monitor.Snapshot().Targets[0].Risk!.Level);
            Assert.Contains("sentinel_target_risk_level{target_id=\"discord-bot\"} 0",monitor.Metrics());
            clock.Now += TimeSpan.FromSeconds(151);
            Assert.Equal("STALE",monitor.Snapshot().Targets[0].Status);
            Assert.Equal("UNKNOWN",monitor.Snapshot().Targets[0].Risk!.Level);
            Assert.Contains("sentinel_target_risk_level{target_id=\"discord-bot\"} -1",monitor.Metrics());
        }
        finally {if(File.Exists(file))File.Delete(file);}
    }
    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now = DateTimeOffset.Parse("2026-10-13T08:00:00Z");
        public override DateTimeOffset GetUtcNow() => Now;
    }
    [Fact]
    public void DedicatedTokenCannotUpdateOtherTargetsAndMissingReportsExpire()
    {
        var clock = new Clock();
        var target = new PlatformTarget { TargetId = "discord-bot", Name = "Bot", Role = "DISCORD", Track = "CONTROL",
            Kind = "service_heartbeat", AuthTokenEnv = "SENTINEL_TEST_HEARTBEAT_TOKEN" };
        var store = new HeartbeatStore(Options.Create(new PlatformOptions { Targets = [target] }), clock);
        var token = new string('a', 40);
        Environment.SetEnvironmentVariable(target.AuthTokenEnv, token);
        try
        {
            Assert.True(target.IsValid());
            Assert.False(store.Authorize("discord-bot", "Bearer wrong"));
            Assert.False(store.Authorize("backend", "Bearer " + token));
            Assert.True(store.Authorize("discord-bot", "Bearer " + token));
            Assert.Equal("PENDING", store.Read(target).Status);
            var value = new ServiceHeartbeat(clock.Now, "active", 0, 25, 50, 0.1, "unverified");
            Assert.False(store.Accept("backend", value));
            Assert.True(store.Accept("discord-bot", value));
            Assert.False(store.Accept("discord-bot", value));
            Assert.Equal("HEALTHY", store.Read(target).Status);
            Assert.Equal("unverified", store.Read(target).Components["gateway_state"]);
            clock.Now += TimeSpan.FromSeconds(151);
            Assert.Equal("STALE", store.Read(target).Status);
            Assert.False(store.Accept("discord-bot", value));
            Assert.False(store.Accept("discord-bot", value with { CheckedAt = clock.Now, DiskUsedPercent = double.NaN }));
            Assert.True(store.Accept("discord-bot", value with { CheckedAt = clock.Now, ServiceState = "failed" }));
            Assert.Equal("DOWN", store.Read(target).Status);
        }
        finally { Environment.SetEnvironmentVariable(target.AuthTokenEnv, null); }
    }
}
