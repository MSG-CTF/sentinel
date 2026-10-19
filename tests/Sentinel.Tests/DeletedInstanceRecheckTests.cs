using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Instances;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class DeletedInstanceRecheckTests
{
    [Fact]
    public async Task ConfirmedDeletionIsReusedWithoutAdvancingItsEvidenceTime()
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        var original = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("REMOVED", original.Status);
        Assert.Equal(original.RuntimeObservedAt, original.DeletionVerifiedAt);
        Assert.Equal(original.DeletionVerifiedAt!.Value.AddMinutes(5), original.DeletionRecheckAt);
        for (var index = 0; index < 9; index++)
        {
            lab.Clock.Now = lab.Clock.Now.AddSeconds(30);
            await lab.Inventory.RefreshAsync();
            Assert.Equal(original, Assert.Single(lab.Inventory.Snapshot().Instances));
        }
        Assert.Equal(10, lab.SchedulerCalls);
        Assert.Equal(1, lab.RuntimeCalls);
        var json = JsonSerializer.Serialize(lab.Inventory.Snapshot(), PlatformJson.Options);
        Assert.Contains("\"deletion_verified_at\"", json);
        Assert.Contains("\"deletion_recheck_at\"", json);
        Assert.DoesNotContain("test-credential", json);
        Assert.DoesNotContain("namespace", json);
        lab.Clock.Now = lab.Clock.Now.AddSeconds(30);
        await lab.Inventory.RefreshAsync();
        Assert.Equal(2, lab.RuntimeCalls);
        Assert.Equal(lab.Clock.Now, Assert.Single(lab.Inventory.Snapshot().Instances).DeletionVerifiedAt);
    }

    [Theory]
    [InlineData("RUNNING")]
    [InlineData("PROVISIONING")]
    [InlineData("CLEANUP_PENDING")]
    public async Task ActiveOrCleanupPendingWorkloadsAreQueriedEveryRefresh(string state)
    {
        using var lab = new Lab();
        lab.Row["status"] = state;
        for (var index = 0; index < 3; index++) await lab.Inventory.RefreshAsync();
        Assert.Equal(3, lab.RuntimeCalls);
        var row = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("DOWN", row.Status);
        Assert.Equal("ACTIVE_WORKLOAD_TERMINATED", row.ReasonCode);
        Assert.Null(row.DeletionVerifiedAt);
    }

    [Theory]
    [InlineData("status")]
    [InlineData("workload")]
    [InlineData("target")]
    [InlineData("expiry")]
    [InlineData("challenge")]
    [InlineData("origin")]
    [InlineData("credential")]
    [InlineData("credential_name")]
    public async Task ChangedOwnershipStateOrCredentialInvalidatesDeletion(string field)
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        switch (field)
        {
            case "status": lab.Row["status"] = "RUNNING"; break;
            case "workload": lab.Row["runtime_workload_id"] = "test-cluster/new-namespace"; break;
            case "target": lab.Row["runtime_target_id"] = "other-cluster"; lab.Options.RuntimeTargets[0].TargetId = "other-cluster"; break;
            case "expiry": lab.Row["expires_at"] = lab.Clock.Now.AddMinutes(10); break;
            case "challenge": lab.Row["challenge_id"] = Guid.NewGuid(); break;
            case "origin": lab.Options.RuntimeTargets[0].BaseUrl = "https://runtime-new.test/"; break;
            case "credential": Environment.SetEnvironmentVariable(lab.TokenKey, "rotated-test-credential"); break;
            case "credential_name": lab.Options.RuntimeTargets[0].AuthTokenEnv = lab.OtherTokenKey; break;
        }
        await lab.Inventory.RefreshAsync();
        Assert.Equal(2, lab.RuntimeCalls);
    }

    [Theory]
    [InlineData(401)]
    [InlineData(404)]
    [InlineData(503)]
    public async Task FailedRecheckCannotFallBackToOldDeletion(int code)
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        lab.Clock.Now = lab.Clock.Now.AddMinutes(5);
        lab.RuntimeCode = (HttpStatusCode)code;
        await lab.Inventory.RefreshAsync();
        var row = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("UNVERIFIED", row.Status);
        Assert.Null(row.DeletionVerifiedAt);
        Assert.Null(row.DeletionRecheckAt);
        lab.RuntimeCode = HttpStatusCode.OK;
        await lab.Inventory.RefreshAsync();
        Assert.Equal(3, lab.RuntimeCalls);
        Assert.Equal("REMOVED", Assert.Single(lab.Inventory.Snapshot().Instances).Status);
    }

    [Theory]
    [InlineData("unavailable")]
    [InlineData("removed_row")]
    [InlineData("disabled")]
    public async Task MissingInventoryCannotPreserveADeletionCache(string fault)
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        if (fault == "unavailable") lab.SchedulerCode = HttpStatusCode.ServiceUnavailable;
        if (fault == "removed_row") lab.Empty = true;
        if (fault == "disabled") lab.Options.Enabled = false;
        await lab.Inventory.RefreshAsync();
        Assert.Empty(lab.Inventory.Snapshot().Instances);
        lab.Options.Enabled = true; lab.Empty = false; lab.SchedulerCode = HttpStatusCode.OK;
        await lab.Inventory.RefreshAsync();
        Assert.Equal(2, lab.RuntimeCalls);
    }

    [Fact]
    public async Task ResourceReappearanceIsDetectedAtTheNextScheduledVerification()
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        lab.Phase = "READY";
        lab.Clock.Now = lab.Clock.Now.AddMinutes(5);
        await lab.Inventory.RefreshAsync();
        var row = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("DOWN", row.Status);
        Assert.Equal("TERMINAL_RESOURCE_PRESENT", row.ReasonCode);
        Assert.Null(row.DeletionVerifiedAt);
        await lab.Inventory.RefreshAsync();
        Assert.Equal(3, lab.RuntimeCalls);
    }

    [Fact]
    public async Task CollectorStalenessAndProcessRestartStillRequireNewEvidence()
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        lab.Clock.Now = lab.Clock.Now.AddMinutes(4);
        Assert.Equal("STALE", lab.Inventory.Snapshot().Status);
        Assert.Null(Assert.Single(lab.Inventory.Snapshot().Instances).DeletionVerifiedAt);
        var restarted = lab.NewInventory();
        await restarted.RefreshAsync();
        Assert.Equal(2, lab.RuntimeCalls);
        Assert.Equal(lab.Clock.Now, Assert.Single(restarted.Snapshot().Instances).DeletionVerifiedAt);
    }

    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now = new(2026, 10, 19, 0, 0, 0, TimeSpan.Zero);
        public override DateTimeOffset GetUtcNow() => Now;
    }

    private sealed class Lab : IDisposable
    {
        public Clock Clock { get; } = new();
        public string TokenKey { get; } = "DELETION_TEST_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        public string OtherTokenKey { get; } = "DELETION_OTHER_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        public Dictionary<string, object?> Row { get; }
        public int SchedulerCalls, RuntimeCalls;
        public bool Empty;
        public string Phase = "TERMINATED";
        public HttpStatusCode RuntimeCode = HttpStatusCode.OK, SchedulerCode = HttpStatusCode.OK;
        public InstanceInventoryOptions Options { get; }
        public InstanceInventory Inventory { get; }
        private readonly HttpClient _client;

        public Lab()
        {
            Environment.SetEnvironmentVariable(TokenKey, "test-credential");
            Environment.SetEnvironmentVariable(OtherTokenKey, "test-credential");
            Row = new() { ["instance_id"] = Guid.NewGuid(), ["challenge_id"] = Guid.NewGuid(), ["status"] = "CLEANED",
                ["runtime_type"] = "KUBERNETES", ["runtime_target_id"] = "test-cluster", ["runtime_workload_id"] = "test-cluster/namespace",
                ["expires_at"] = Clock.Now.AddHours(1), ["hard_expires_at"] = Clock.Now.AddHours(2) };
            Options = new() { Enabled = true, SchedulerUrl = "https://scheduler.test/api/instances", SchedulerTokenEnv = TokenKey,
                RuntimeTargets = [new() { TargetId = "test-cluster", BaseUrl = "https://runtime.test/", AuthTokenEnv = TokenKey }] };
            _client = new(new Handler(request =>
            {
                Assert.NotNull(request.Headers.Authorization);
                if (request.RequestUri!.Host == "scheduler.test")
                {
                    SchedulerCalls++;
                    return new(SchedulerCode) { Content = JsonContent.Create(new { code = "SUCCESS", data = new
                        { items = Empty ? Array.Empty<Dictionary<string, object?>>() : new[] { Row }, next_cursor = (string?)null } }) };
                }
                RuntimeCalls++;
                Assert.StartsWith("/internal/v1/instances/", request.RequestUri.AbsolutePath);
                return new(RuntimeCode) { Content = JsonContent.Create(new
                {
                    instance_id = Row["instance_id"], target_id = Row["runtime_target_id"], runtime_workload_id = Row["runtime_workload_id"],
                    phase = Phase, observed_at = Clock.Now, endpoint_ready = true, metrics_available = false,
                    node = new { ready = true, memory_pressure = false, disk_pressure = false, pid_pressure = false },
                    containers = new[] { new { name = "web", pod_name = "web-1", state = "RUNNING", ready = true, restart_count = 0 } },
                }) };
            }));
            Inventory = NewInventory();
        }

        public InstanceInventory NewInventory() => new(_client, Microsoft.Extensions.Options.Options.Create(Options), Clock);
        public void Dispose()
        {
            _client.Dispose(); Environment.SetEnvironmentVariable(TokenKey, null); Environment.SetEnvironmentVariable(OtherTokenKey, null);
        }
    }

    private sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => Task.FromResult(respond(request));
    }
}
