using System.Net;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Sentinel.Monitor.Instances;
using Xunit;

namespace Sentinel.Tests;

[Collection("Discord environment")]
public sealed class ChallengeHeartbeatTests
{
    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now = DateTimeOffset.Parse("2026-10-19T03:00:00Z");
        public override DateTimeOffset GetUtcNow() => Now;
    }
    internal static PlatformTarget Target(DateTimeOffset now) => new()
    {
        TargetId = "owned-notebook", Name = "실습 문제 기능 점검", Role = "CHALLENGE", Track = "TEST",
        Kind = "challenge_heartbeat", AuthTokenEnv = "TEST_OWNED_NOTEBOOK_REPORTER",
        ExpectedFields = new()
        {
            ["instance_id"] = "11111111-1111-4111-8111-111111111111", ["challenge_id"] = "22222222-2222-4222-8222-222222222222",
            ["release_id"] = "33333333-3333-4333-8333-333333333333", ["owner_team_id"] = "44444444-4444-4444-8444-444444444444",
            ["runtime_target_id"] = "owned-cluster", ["runtime_workload_id"] = "owned-cluster/owned-namespace",
            ["profile"] = "web-notebook", ["source_revision"] = ChallengeHeartbeatStore.SourceRevision,
            ["expires_at"] = now.AddMinutes(15).ToString("O"),
        },
    };
    internal static ChallengeHeartbeat Sample(PlatformTarget target, DateTimeOffset at) => new(at,
        target.ExpectedFields["instance_id"], target.ExpectedFields["challenge_id"], target.ExpectedFields["release_id"],
        target.ExpectedFields["owner_team_id"], target.ExpectedFields["runtime_target_id"], target.ExpectedFields["runtime_workload_id"],
        "web-notebook", ChallengeHeartbeatStore.SourceRevision, "owned_deployment_test", "UP", "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED", 25);

    [Fact]
    public void IdentitySourceScopeTimeAndReasonAreAllBoundAndReplayIsRejected()
    {
        var clock = new Clock(); var target = Target(clock.Now);
        var store = new ChallengeHeartbeatStore(Options.Create(new PlatformOptions { Targets = [target] }), clock);
        var valid = Sample(target, clock.Now);
        foreach (var value in new[] {
            valid with { InstanceId = Guid.NewGuid().ToString() }, valid with { ChallengeId = Guid.NewGuid().ToString() },
            valid with { ReleaseId = Guid.NewGuid().ToString() }, valid with { OwnerTeamId = Guid.NewGuid().ToString() },
            valid with { RuntimeTargetId = "other" }, valid with { RuntimeWorkloadId = "other" }, valid with { Profile = "pwn-note" },
            valid with { SourceRevision = new string('a', 40) }, valid with { Scope = "participant" },
            valid with { CheckedAt = clock.Now.AddSeconds(31) }, valid with { CheckedAt = clock.Now.AddSeconds(-91) },
            valid with { Status = "HEALTHY" }, valid with { ElapsedMs = -1 }, valid with { ElapsedMs = 30001 },
            valid with { ReasonCode = "MSG{secret}" }, valid with { Status = "UP", ReasonCode = "FUNCTION_TIMEOUT" },
            valid with { Status = "CHECKER_ERROR", ReasonCode = "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED" },
        }) Assert.False(store.Accept(target.TargetId, value));
        Assert.True(store.Accept(target.TargetId, valid)); Assert.False(store.Accept(target.TargetId, valid));
        Assert.Equal("HEALTHY", store.Read(target).Status);
        clock.Now += TimeSpan.FromSeconds(91); Assert.Equal("STALE", store.Read(target).Status);
        clock.Now += TimeSpan.FromMinutes(15); Assert.Equal("UNVERIFIED", store.Read(target).Status);
        Assert.False(store.Accept(target.TargetId, Sample(target, clock.Now)));
    }

    [Fact]
    public void CredentialsAndOptionsCannotAliasAnotherTargetOrOutliveTheLease()
    {
        var clock = new Clock(); var target = Target(clock.Now); var token = new string('o', 48);
        var options = new PlatformOptions { Targets = [target] };
        var old = Environment.GetEnvironmentVariable(target.AuthTokenEnv!);
        Environment.SetEnvironmentVariable(target.AuthTokenEnv!, token);
        try
        {
            var store = new ChallengeHeartbeatStore(Options.Create(options), clock);
            Assert.True(options.IsValid()); Assert.True(store.Authorize(target.TargetId, "Bearer " + token));
            Assert.False(store.Authorize("other", "Bearer " + token)); Assert.False(store.Authorize(target.TargetId, "Bearer " + token + ", other"));
            var other = Target(clock.Now); other.TargetId = "other";
            options.Targets.Add(other); Assert.False(options.IsValid());
            other.ExpectedFields["instance_id"] = Guid.NewGuid().ToString(); Assert.False(options.IsValid());
            other.AuthTokenEnv = "ANOTHER_OWNED_TOKEN"; Assert.True(options.IsValid());
            clock.Now += TimeSpan.FromMinutes(16); Assert.False(store.Authorize(target.TargetId, "Bearer " + token));
            var unbound = Target(clock.Now); unbound.ExpectedFields.Remove("release_id"); Assert.False(unbound.IsValid());
            unbound = Target(clock.Now); unbound.Url = "https://untrusted.example/"; Assert.False(unbound.IsValid());
        }
        finally { Environment.SetEnvironmentVariable(target.AuthTokenEnv!, old); }
    }

    [Fact]
    public async Task FunctionalFailureAlertsOnceAndUnknownCacheOrRestartCannotInventRecovery()
    {
        var clock = new Clock(); var target = Target(clock.Now);
        var root = Path.Combine(Path.GetTempPath(), "owned-functional-" + Guid.NewGuid()); Directory.CreateDirectory(root);
        var options = Options.Create(new PlatformOptions { Targets = [target], EventFile = Path.Combine(root, "events.jsonl") });
        var handler = new Delivery(); var old = Environment.GetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL");
        Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/12345678901234567/" + new string('a', 40));
        try
        {
            var store = new ChallengeHeartbeatStore(options, clock);
            var monitor = new PlatformMonitor(options, new PlatformProbeClient(new HttpClient(), timeProvider: clock,
                challengeHeartbeatStore: store), NullLogger<PlatformMonitor>.Instance, clock);
            DiscordAlerts Create() => new(Options.Create(new DiscordAlertOptions { Enabled = true, StateFile = Path.Combine(root, "alerts.json") }), new HttpClient(handler), clock);
            var alerts = Create();
            async Task Poll() { await monitor.RefreshAsync(); alerts.Observe(monitor.Snapshot()); await alerts.DeliverAsync(); }
            void Send(string status, string reason)
            {
                clock.Now += TimeSpan.FromSeconds(15);
                Assert.True(store.Accept(target.TargetId, Sample(target, clock.Now) with { Status = status, ReasonCode = reason }));
            }
            Send("UP", "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED"); await Poll(); Assert.Empty(handler.Bodies);
            Send("DOWN", "FUNCTION_RESPONSE_MISMATCH"); await Poll(); await Poll(); Assert.Single(handler.Bodies);
            alerts = Create(); Send("DOWN", "FUNCTION_RESPONSE_MISMATCH"); await Poll(); Assert.Single(handler.Bodies);
            Send("UP", "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED"); await Poll(); await Poll(); Assert.Single(handler.Bodies);
            clock.Now += TimeSpan.FromSeconds(91); await Poll(); Assert.Equal("STALE", monitor.Snapshot().Targets[0].Status);
            Assert.Single(handler.Bodies);
            Send("CHECKER_ERROR", "CONTROL_PLANE_UNVERIFIED"); await Poll(); Assert.Single(handler.Bodies);
            Send("UP", "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED"); await Poll(); Assert.Single(handler.Bodies);
            Send("UP", "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED"); await Poll(); Assert.Equal(2, handler.Bodies.Count);
            await Poll(); Send("UP", "DATABASE_LOGIN_AND_ACCESS_RULES_CONFIRMED"); await Poll(); Assert.Equal(2, handler.Bodies.Count);
            Assert.Contains("복구 확인", handler.Bodies[1]);
            Assert.DoesNotContain("Authorization", JsonSerializer.Serialize(monitor.Snapshot(), PlatformJson.Options));
            Assert.Equal("PENDING", new ChallengeHeartbeatStore(options, clock).Read(target).Status);
        }
        finally { Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL", old); Directory.Delete(root, true); }
    }
    private sealed class Delivery : HttpMessageHandler
    {
        public List<string> Bodies = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        { Bodies.Add(await request.Content!.ReadAsStringAsync(token)); return new(HttpStatusCode.OK); }
    }

    [Theory]
    [InlineData("valid", "UP")]
    [InlineData("release", "CHECKER_ERROR")]
    [InlineData("team", "CHECKER_ERROR")]
    [InlineData("workload", "UNVERIFIED")]
    [InlineData("unregistered", "UNVERIFIED")]
    public async Task InventoryRequiresCurrentSchedulerAndRuntimeOwnershipWithoutFollowingWorkloadUrls(string scenario, string health)
    {
        var clock = new Clock(); var target = Target(clock.Now); var f = target.ExpectedFields;
        var store = new ChallengeHeartbeatStore(Options.Create(new PlatformOptions { Targets = [target] }), clock);
        Assert.True(store.Accept(target.TargetId, Sample(target, clock.Now)));
        var row = new Dictionary<string, object?>
        {
            ["instance_id"] = scenario == "unregistered" ? Guid.NewGuid().ToString() : f["instance_id"],
            ["challenge_id"] = f["challenge_id"], ["release_id"] = scenario == "release" ? Guid.NewGuid().ToString() : f["release_id"],
            ["team_id"] = scenario == "team" ? Guid.NewGuid().ToString() : f["owner_team_id"],
            ["status"] = "RUNNING", ["runtime_type"] = "KUBERNETES", ["runtime_target_id"] = f["runtime_target_id"],
            ["runtime_workload_id"] = scenario == "workload" ? "other" : f["runtime_workload_id"],
            ["expires_at"] = f["expires_at"], ["hard_expires_at"] = f["expires_at"], ["idle_expires_at"] = null,
            ["service_url"] = "http://must-not-follow.invalid/private",
        };
        var runtime = new
        {
            instance_id = row["instance_id"], target_id = f["runtime_target_id"], runtime_workload_id = f["runtime_workload_id"],
            phase = "READY", observed_at = clock.Now, endpoint_ready = true, metrics_available = false,
            node = new { ready = true, memory_pressure = false, disk_pressure = false, pid_pressure = false },
            containers = new[] { new { name = "web", pod_name = "owned-web", state = "RUNNING", ready = true, restart_count = 0 } },
        };
        var key = "OWNED_INVENTORY_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        Environment.SetEnvironmentVariable(key, "fixture-control-token");
        using var handler = new InventoryHandler(row, runtime);
        using var client = new HttpClient(handler);
        try
        {
            var inventory = new InstanceInventory(client, Options.Create(new InstanceInventoryOptions
            {
                Enabled = true, SchedulerUrl = "http://scheduler.local/api/instances", SchedulerTokenEnv = key,
                RuntimeTargets = [new() { TargetId = f["runtime_target_id"], BaseUrl = "http://runtime.local", AuthTokenEnv = key }],
            }), clock, store);
            await inventory.RefreshAsync(); var observed = Assert.Single(inventory.Snapshot().Instances);
            Assert.Equal(health, observed.ChallengeHealth); Assert.Equal(2, handler.Requests);
            if (scenario == "valid") Assert.Equal("owned_deployment_test", observed.FunctionalCheck!.Scope);
        }
        finally { Environment.SetEnvironmentVariable(key, null); }
    }
    private sealed class InventoryHandler(object row, object runtime) : HttpMessageHandler
    {
        public int Requests;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Requests++;
            Assert.Contains(request.RequestUri!.Host, new[] { "scheduler.local", "runtime.local" });
            Assert.NotNull(request.Headers.Authorization);
            var body = request.RequestUri.Host == "scheduler.local"
                ? JsonSerializer.Serialize(new { code = "SUCCESS", data = new { items = new[] { row }, next_cursor = (string?)null } })
                : JsonSerializer.Serialize(runtime);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                { Content = new StringContent(body, System.Text.Encoding.UTF8, "application/json") });
        }
    }
}
