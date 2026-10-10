using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Sentinel.Monitor.Recovery;
using Xunit;

namespace Sentinel.Tests;

public sealed class RecoveryTests
{
    [Fact]
    public void HealthyBaselineAndThreeDistinctFailuresAreRequired()
    {
        using var fixture = new Fixture();
        fixture.Step("DOWN", "CONNECTION_FAILED", 5);
        Assert.Empty(fixture.Snapshot.Requests);
        fixture.Step("HEALTHY", "BACKEND_READY", 2);
        fixture.Step("DOWN", "CONNECTION_FAILED", 2);
        Assert.Empty(fixture.Snapshot.Requests);
        fixture.Recovery.Observe(fixture.Last!); // A repeated sample does not advance a streak.
        Assert.Empty(fixture.Snapshot.Requests);
        fixture.Step("DOWN", "CONNECTION_FAILED");
        Assert.Equal("QUEUED", Assert.Single(fixture.Snapshot.Requests).Status);
        fixture.Step("DOWN", "CONNECTION_FAILED", 20);
        Assert.Single(fixture.Snapshot.Requests);
    }

    [Theory]
    [InlineData("DOWN", "TIMEOUT")]
    [InlineData("DOWN", "DATABASE_DOWN")]
    [InlineData("DOWN", "CACHE_DOWN")]
    [InlineData("DOWN", "HTTP_ERROR")]
    [InlineData("UNVERIFIED", "AUTH_REQUIRED")]
    [InlineData("STALE", "MONITOR_REFRESH_STALE")]
    public void OtherSignalsCannotRequestAContainerStart(string status, string reason)
    {
        using var fixture = new Fixture();
        fixture.Step("HEALTHY", "BACKEND_READY", 2);
        fixture.Step(status, reason, 5);
        Assert.Empty(fixture.Snapshot.Requests);
    }

    [Fact]
    public async Task ClaimIsExclusiveAndWrongExecutorCannotClaim()
    {
        using var fixture = new Fixture();
        var request = fixture.Queue();
        Assert.Null(fixture.Recovery.Claim(request.RequestId, "other-executor"));
        var claims = await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => Task.Run(() =>
            fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId))));
        Assert.Single(claims.Where(claim => claim is not null));
    }

    [Fact]
    public void CommandAcknowledgementNeedsTwoNewFunctionalChecks()
    {
        using var fixture = new Fixture();
        var claim = fixture.Claim();
        var result = new RecoveryResult(RecoveryOptions.ExecutorId, claim.ClaimId, "CONTAINER_STARTED");
        Assert.False(fixture.Recovery.Complete(claim.Request.RequestId, result with { ClaimId = "wrong" }));
        Assert.True(fixture.Recovery.Complete(claim.Request.RequestId, result));
        Assert.Equal("VERIFYING", fixture.Snapshot.Requests[0].Status);
        fixture.Step("HEALTHY", "BACKEND_READY");
        Assert.Equal("VERIFYING", fixture.Snapshot.Requests[0].Status);
        fixture.Step("HEALTHY", "BACKEND_READY");
        Assert.Equal("SUCCEEDED", fixture.Snapshot.Requests[0].Status);
        Assert.True(fixture.Recovery.Complete(claim.Request.RequestId, result));
        Assert.False(fixture.Recovery.Complete(claim.Request.RequestId, result with { ResultCode = "OUTCOME_UNKNOWN" }));
        Assert.DoesNotContain(claim.ClaimId, JsonSerializer.Serialize(fixture.Snapshot, PlatformJson.Options));
    }

    [Fact]
    public void ADelayedObservationFromBeforeExecutionDoesNotCountTowardVerification()
    {
        using var fixture = new Fixture();
        var claim = fixture.Claim();
        fixture.Clock.Now = fixture.Clock.Now.AddSeconds(2);
        Assert.True(fixture.Recovery.Complete(claim.Request.RequestId,
            new(RecoveryOptions.ExecutorId, claim.ClaimId, "CONTAINER_STARTED")));
        var old = fixture.Last!;
        fixture.Recovery.Observe(old with { Targets = [old.Targets[0] with
            { Status = "HEALTHY", ReasonCode = "BACKEND_READY", CheckedAt = fixture.Clock.Now.AddSeconds(-1) }] });
        fixture.Step("HEALTHY", "BACKEND_READY");
        Assert.Equal("VERIFYING", fixture.Snapshot.Requests[0].Status);
        fixture.Step("HEALTHY", "BACKEND_READY");
        Assert.Equal("SUCCEEDED", fixture.Snapshot.Requests[0].Status);
    }

    [Fact]
    public void BlockedExecutionCannotLoopDuringTheSameIncident()
    {
        using var fixture = new Fixture();
        var claim = fixture.Claim();
        Assert.True(fixture.Recovery.Complete(claim.Request.RequestId,
            new(RecoveryOptions.ExecutorId, claim.ClaimId, "PRECONDITION_NOT_MET")));
        fixture.Clock.Now = fixture.Clock.Now.AddHours(1);
        fixture.Step("DOWN", "CONNECTION_FAILED", 5);
        Assert.Single(fixture.Snapshot.Requests);
        fixture.Step("HEALTHY", "BACKEND_READY", 2);
        fixture.Step("DOWN", "CONNECTION_FAILED", 3);
        Assert.Equal(2, fixture.Snapshot.Requests.Count);
    }

    [Fact]
    public void ExpiredAndStaleRequestsCannotBeClaimed()
    {
        using var fixture = new Fixture();
        var request = fixture.Queue();
        fixture.Clock.Now = fixture.Clock.Now.AddMinutes(3);
        Assert.Null(fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId));
        fixture.Recovery.Observe(fixture.Last!);
        Assert.Equal("EXPIRED", fixture.Snapshot.Requests[0].Status);
        Assert.False(fixture.Snapshot.Armed);
    }

    [Fact]
    public void ChangedEvidenceCancelsQueuedWork()
    {
        using var fixture = new Fixture();
        var request = fixture.Queue();
        fixture.Step("DOWN", "DATABASE_DOWN");
        Assert.Equal("CANCELLED", fixture.Snapshot.Requests[0].Status);
        Assert.Null(fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId));
    }

    [Fact]
    public void MonitorStorageFailurePreventsDispatch()
    {
        using var fixture = new Fixture();
        var request = fixture.Queue();
        fixture.Recovery.Observe(fixture.Last! with { EventStorageHealthy = false });
        Assert.Null(fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId));
    }

    [Fact]
    public void JournalSurvivesRestartWithoutReplayingClaimedWork()
    {
        using var fixture = new Fixture();
        var claim = fixture.Claim();
        fixture.Restart();
        fixture.Step("DOWN", "CONNECTION_FAILED", 5);
        Assert.Null(fixture.Recovery.Poll());
        Assert.Null(fixture.Recovery.Claim(claim.Request.RequestId, RecoveryOptions.ExecutorId));
        Assert.True(fixture.Recovery.Complete(claim.Request.RequestId,
            new(RecoveryOptions.ExecutorId, claim.ClaimId, "OUTCOME_UNKNOWN")));
        Assert.Single(fixture.Snapshot.Requests);
    }

    [Fact]
    public void CorruptOrUnwritableJournalDisablesExecution()
    {
        using var fixture = new Fixture();
        File.WriteAllText(fixture.Options.StateFile, "{broken");
        fixture.Restart();
        fixture.Step("HEALTHY", "BACKEND_READY", 2);
        fixture.Step("DOWN", "CONNECTION_FAILED", 3);
        Assert.False(fixture.Snapshot.StorageHealthy);
        Assert.Empty(fixture.Snapshot.Requests);
        File.Delete(fixture.Options.StateFile);
        fixture.Options.StateFile = fixture.Directory;
        fixture.Restart();
        fixture.Step("HEALTHY", "BACKEND_READY");
        Assert.False(fixture.Snapshot.StorageHealthy);
    }

    [Fact]
    public void DisabledAndNonLocalConfigurationsCannotDispatch()
    {
        using var fixture = new Fixture();
        fixture.Options.Enabled = false;
        fixture.Restart();
        fixture.Step("HEALTHY", "BACKEND_READY", 2);
        fixture.Step("DOWN", "CONNECTION_FAILED", 5);
        Assert.Empty(fixture.Snapshot.Requests);
        Assert.Equal("observe_only", fixture.Recovery.Mode);
        fixture.Options.Enabled = true;
        fixture.Platform.Profile = "deployed";
        Assert.False(fixture.Options.IsValid(fixture.Platform));
        fixture.Platform.Profile = "local-recovery";
        fixture.Platform.Targets[0].Url = "https://example.com/healthz";
        Assert.False(fixture.Options.IsValid(fixture.Platform));
    }

    [Fact]
    public void RecoveryBodiesRequireSnakeCaseAndRejectExtraInstructions()
    {
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<RecoveryClaim>(
            "{\"executorId\":\"local-docker\"}", PlatformJson.Options));
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<RecoveryResult>(
            "{\"executor_id\":\"local-docker\",\"command\":\"anything\"}", PlatformJson.Options));
    }

    [Theory]
    [InlineData("internal-backend")]
    [InlineData("external-backend")]
    [InlineData("scheduler")]
    [InlineData("broker")]
    [InlineData("internal-frontend")]
    [InlineData("external-frontend")]
    [InlineData("edge")]
    [InlineData("runtime")]
    public void EachAssetHasItsOwnBaselineClaimAndVerification(string assetId)
    {
        using var fixture = new Fixture(all: true);
        fixture.StepAll(count: 2);
        if (assetId == "runtime") Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("runtime", true)])));
        fixture.StepAll(new() { [assetId] = ("DOWN", "CONNECTION_FAILED") }, 3);
        var request = Assert.Single(fixture.Snapshot.Requests);
        Assert.Equal(assetId, request.AssetId);
        Assert.Equal(RecoveryBindings.All.Single(binding => binding.AssetId == assetId).Track, request.Track);
        var claim = Assert.IsType<ClaimedRecovery>(fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId));
        Assert.True(fixture.Recovery.Complete(request.RequestId, new(RecoveryOptions.ExecutorId, claim.ClaimId, "CONTAINER_STARTED")));
        fixture.StepAll();
        Assert.Equal("VERIFYING", fixture.Snapshot.Requests[0].Status);
        fixture.StepAll();
        Assert.Equal("SUCCEEDED", fixture.Snapshot.Requests[0].Status);
        Assert.False(fixture.Snapshot.Assets.Single(asset => asset.AssetId == assetId).Armed);
        Assert.All(fixture.Snapshot.Assets.Where(asset => asset.AssetId != assetId), asset => Assert.True(asset.Armed));
    }

    [Theory]
    [InlineData("CONNECTION_FAILED")]
    [InlineData("TIMEOUT")]
    public void RuntimeWaitsForFreshHostConfirmationBeforeConsumingAnIncident(string reason)
    {
        using var fixture = new Fixture(all: true);
        fixture.StepAll(count: 2);
        var down = new Dictionary<string, (string, string)> { ["runtime"] = ("DOWN", reason), ["runtime-db"] = ("DOWN", reason) };
        fixture.StepAll(down, 3);
        Assert.Empty(fixture.Snapshot.Requests);
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("runtime", false)])));
        fixture.StepAll(down, 3);
        Assert.Empty(fixture.Snapshot.Requests);
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("runtime", true)])));
        fixture.Clock.Now = fixture.Clock.Now.AddSeconds(16);
        fixture.StepAll(down, 3);
        Assert.Empty(fixture.Snapshot.Requests);
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("runtime", true)])));
        fixture.StepAll(down, 3);
        var request = Assert.Single(fixture.Snapshot.Requests);
        Assert.Equal("runtime", request.AssetId);
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("runtime", false)])));
        Assert.Null(fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId));
    }

    [Fact]
    public void SimultaneousFailuresAreSerializedWithoutSharingCooldowns()
    {
        using var fixture = new Fixture(all: true);
        fixture.StepAll(count: 2);
        var failures = new Dictionary<string, (string, string)> { ["internal-backend"] = ("DOWN", "CONNECTION_FAILED"), ["external-backend"] = ("DOWN", "CONNECTION_FAILED") };
        fixture.StepAll(failures, 3);
        var first = Assert.Single(fixture.Snapshot.Requests);
        Assert.Equal("internal-backend", first.AssetId);
        var claim = fixture.Recovery.Claim(first.RequestId, RecoveryOptions.ExecutorId)!;
        fixture.StepAll(failures, 3);
        Assert.Single(fixture.Snapshot.Requests);
        fixture.Recovery.Complete(first.RequestId, new(RecoveryOptions.ExecutorId, claim.ClaimId, "PRECONDITION_NOT_MET"));
        fixture.StepAll(failures);
        Assert.Equal(2, fixture.Snapshot.Requests.Count);
        Assert.Equal("external-backend", fixture.Snapshot.Requests[0].AssetId);
    }

    [Fact]
    public void FrontendCannotDispatchWhileItsBackendIsUnhealthy()
    {
        using var fixture = new Fixture(all: true);
        fixture.StepAll(count: 2);
        fixture.StepAll(new() { ["internal-backend"] = ("DOWN", "DATABASE_DOWN"), ["internal-frontend"] = ("DOWN", "CONNECTION_FAILED") }, 5);
        Assert.Empty(fixture.Snapshot.Requests);
        fixture.StepAll(new() { ["internal-frontend"] = ("DOWN", "CONNECTION_FAILED") }, 3);
        Assert.Equal("internal-frontend", Assert.Single(fixture.Snapshot.Requests).AssetId);
    }

    [Fact]
    public void DatabaseVerificationNeedsDistinctSamplesAfterExecution()
    {
        using var fixture = new Fixture(all: true);
        fixture.StepAll(count: 2);
        fixture.StepAll(new() { ["scheduler"] = ("DOWN", "CONNECTION_FAILED") }, 3);
        var request = fixture.Snapshot.Requests[0];
        var claim = fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId)!;
        fixture.Recovery.Complete(request.RequestId, new(RecoveryOptions.ExecutorId, claim.ClaimId, "CONTAINER_STARTED"));
        fixture.StepAll();
        var old = fixture.Last!;
        fixture.Clock.Now = fixture.Clock.Now.AddSeconds(1);
        fixture.Recovery.Observe(old with { Targets = old.Targets.Select(target => target.TargetId == "scheduler" ? target with { CheckedAt = fixture.Clock.Now } : target).ToArray() });
        Assert.Equal("VERIFYING", fixture.Snapshot.Requests[0].Status);
        fixture.StepAll();
        Assert.Equal("SUCCEEDED", fixture.Snapshot.Requests[0].Status);
    }

    [Fact]
    public void VersionOneHistoryAndConsumedClaimsSurviveUpgrade()
    {
        using var fixture = new Fixture(all: true);
        var request = new RecoveryRequest(Guid.NewGuid().ToString(), "internal-backend", "INTERNAL", "local-docker", "start_stopped_service",
            fixture.Clock.Now, fixture.Clock.Now.AddSeconds(120), "CLAIMED", "EXECUTOR_CLAIMED", fixture.Clock.Now);
        var claimId = Guid.NewGuid().ToString();
        File.WriteAllText(fixture.Options.StateFile, JsonSerializer.Serialize(new RecoveryJournal { SchemaVersion = 1,
            Armed = true, Successes = 2, LastRequestedAt = fixture.Clock.Now, Requests = [request], Claims = new() { [request.RequestId] = claimId } }, PlatformJson.Options));
        fixture.Restart();
        Assert.True(fixture.Snapshot.StorageHealthy);
        Assert.Equal(RecoveryBindings.All.Count, fixture.Snapshot.Assets.Count);
        Assert.All(fixture.Snapshot.Assets, asset => Assert.False(asset.Armed));
        Assert.Null(fixture.Recovery.Poll());
        Assert.Null(fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId));
        Assert.True(fixture.Recovery.Complete(request.RequestId, new(RecoveryOptions.ExecutorId, claimId, "OUTCOME_UNKNOWN")));
        Assert.Equal(2, JsonDocument.Parse(File.ReadAllText(fixture.Options.StateFile)).RootElement.GetProperty("schema_version").GetInt32());
    }

    [Fact]
    public void RestartCancelsUnclaimedWorkAndWrongBindingsAreRejected()
    {
        using var fixture = new Fixture();
        fixture.Queue();
        fixture.Restart();
        fixture.Step("DOWN", "CONNECTION_FAILED", 5);
        Assert.Equal("CANCELLED", Assert.Single(fixture.Snapshot.Requests).Status);
        Assert.Null(fixture.Recovery.Poll());
        fixture.Options.EnabledAssets = ["scheduler-db"];
        Assert.False(fixture.Options.IsValid(fixture.Platform));
        fixture.Options.EnabledAssets = ["internal-backend", "internal-backend"];
        Assert.False(fixture.Options.IsValid(fixture.Platform));
    }

    [Fact]
    public void TimeoutNeedsFreshHostConfirmationAndCannotUseAnotherAsset()
    {
        using var fixture = new Fixture(all: true);
        fixture.StepAll(count: 2);
        var timedOut = new Dictionary<string, (string, string)> { ["scheduler"] = ("DOWN", "TIMEOUT") };
        fixture.StepAll(timedOut, 3);
        Assert.Empty(fixture.Snapshot.Requests);
        Assert.False(fixture.Recovery.Inspect(new("wrong", [new("scheduler", true)])));
        Assert.False(fixture.Recovery.Inspect(new("local-docker", [new("scheduler-db", true)])));
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("broker", true)])));
        fixture.StepAll(timedOut, 3);
        Assert.Empty(fixture.Snapshot.Requests);
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("scheduler", true)])));
        fixture.Clock.Now = fixture.Clock.Now.AddSeconds(16);
        fixture.StepAll(timedOut, 3);
        Assert.Empty(fixture.Snapshot.Requests);
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [new("scheduler", true)])));
        fixture.StepAll(timedOut, 3);
        var request = Assert.Single(fixture.Snapshot.Requests);
        Assert.Equal("CONFIRMED_STOPPED_TIMEOUT", request.ReasonCode);
        Assert.True(fixture.Recovery.Inspect(new("local-docker", [])));
        Assert.Null(fixture.Recovery.Claim(request.RequestId, RecoveryOptions.ExecutorId));
        fixture.StepAll(timedOut);
        Assert.Equal("CANCELLED", fixture.Snapshot.Requests[0].Status);
    }

    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now { get; set; } = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }

    private sealed class Fixture : IDisposable
    {
        public string Directory { get; } = Path.Combine(Path.GetTempPath(), "sentinel-recovery-" + Guid.NewGuid());
        public Clock Clock { get; } = new();
        public RecoveryOptions Options { get; }
        public PlatformOptions Platform { get; } = new()
        {
            Profile = "local-recovery", Targets = [new() { TargetId = "internal-backend", Name = "Internal",
                Role = "BACKEND", Track = "INTERNAL", Url = "http://backend-internal:8080/healthz" }]
        };
        public RecoveryCoordinator Recovery { get; private set; } = null!;
        public PlatformSnapshot? Last { get; private set; }
        public RecoverySnapshot Snapshot => Recovery.Snapshot();
        public Fixture(bool all = false)
        {
            System.IO.Directory.CreateDirectory(Directory);
            Options = new() { Enabled = true, StateFile = Path.Combine(Directory, "state.json") };
            if (all)
            {
                Options.EnabledAssets = RecoveryBindings.All.Select(binding => binding.AssetId).ToArray();
                Platform.Targets = RecoveryBindings.All.Select(binding => new PlatformTarget { TargetId = binding.AssetId,
                    Name = binding.AssetId, Role = "SERVICE", Track = binding.Track, Kind = binding.Kind, Url = binding.Url,
                    ExpectedText = "<div id=\"root\"></div>", ExpectedFields = binding.AssetId == "runtime"
                        ? new() { ["instance_id"] = RecoveryBindings.LocalInstance, ["target_id"] = "sentinel-local-k3s" }
                        : new() { ["status"] = "UP", ["scope"] = "local_edge_process" } }).ToList();
                Platform.Targets.AddRange([new() { TargetId = "scheduler-db", Track = "CONTROL", Kind = "json_fields",
                    Url = "http://scheduler:8080/actuator/health/db", ExpectedFields = new() { ["status"] = "UP" } },
                    new() { TargetId = "broker-db", Track = "CONTROL", Kind = "json_fields", Url = "http://broker:8000/health/ready", ExpectedFields = new() { ["status"] = "UP" } },
                    new() { TargetId = "runtime-db", Track = "RUNTIME", Kind = "runtime_operation", Url = "http://runtime:8080/internal/v1/operations/11111111-1111-4111-8111-111111111111",
                        ExpectedFields = new() { ["operation_id"] = "11111111-1111-4111-8111-111111111111" } },
                    new() { TargetId = "linux-pool", Track = "RUNTIME", Kind = "runtime_node", Url = RecoveryBindings.RuntimeStatusUrl,
                        ExpectedFields = new() { ["instance_id"] = RecoveryBindings.LocalInstance, ["target_id"] = "sentinel-local-k3s" } }]);
            }
            Restart();
        }
        public void Restart() => Recovery = new(Microsoft.Extensions.Options.Options.Create(Options),
            Microsoft.Extensions.Options.Options.Create(Platform), Clock);
        public void Step(string status, string reason, int count = 1)
        {
            for (var index = 0; index < count; index++)
            {
                Clock.Now = Clock.Now.AddSeconds(1);
                Recovery.Poll();
                Last = new("local-recovery", "observe_only", Clock.Now, Clock.Now, 15, 3, true,
                    [new("internal-backend", "Internal", "BACKEND", "INTERNAL", "backend_healthz", "http://backend-internal:8080/healthz",
                        true, status, reason, Clock.Now, null, null, new Dictionary<string, string>())]);
                Recovery.Observe(Last);
            }
        }
        public RecoveryRequest Queue()
        {
            Step("HEALTHY", "BACKEND_READY", 2);
            Step("DOWN", "CONNECTION_FAILED", 3);
            return Assert.Single(Snapshot.Requests);
        }
        public void StepAll(Dictionary<string, (string Status, string Reason)>? overrides = null, int count = 1)
        {
            for (var index = 0; index < count; index++)
            {
                Clock.Now = Clock.Now.AddSeconds(1);
                Recovery.Poll();
                Last = new("local-recovery", "automatic_local", Clock.Now, Clock.Now, 15, 3, true,
                    Platform.Targets.Select(target => {
                        var state = overrides?.GetValueOrDefault(target.TargetId) ?? default;
                        return new ProbeObservation(target.TargetId, target.Name, target.Role, target.Track, target.Kind, target.Url,
                            true, state.Status ?? "HEALTHY", state.Reason ?? RecoveryBindings.HealthyReasonFor(target.TargetId),
                            Clock.Now, 200, 1, new Dictionary<string, string>());
                    }).ToArray());
                Recovery.Observe(Last);
            }
        }
        public ClaimedRecovery Claim() => Assert.IsType<ClaimedRecovery>(Recovery.Claim(Queue().RequestId, RecoveryOptions.ExecutorId));
        public void Dispose()
        {
            foreach (var name in new[] { "state.json", "state.json.tmp" }) File.Delete(Path.Combine(Directory, name));
            File.Delete(Directory + ".tmp");
            System.IO.Directory.Delete(Directory);
        }
    }
}
