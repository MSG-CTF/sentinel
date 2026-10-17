using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Instances;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class InstanceInventoryTests
{
    private const string Instance = "11111111-1111-4111-8111-111111111111";
    private const string Challenge = "22222222-2222-4222-8222-222222222222";
    private static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-10-13T04:00:00Z");
    private static JsonObject Row() => new()
    {
        ["instance_id"] = Instance, ["challenge_id"] = Challenge, ["status"] = "RUNNING", ["runtime_type"] = "KUBERNETES",
        ["runtime_target_id"] = "local-k3s", ["runtime_workload_id"] = "local-k3s/namespace",
        ["expires_at"] = Now.AddHours(1).ToString("O"), ["hard_expires_at"] = Now.AddHours(2).ToString("O"),
        ["idle_expires_at"] = null, ["service_url"] = "http://untrusted.invalid/private",
    };
    private static JsonObject Runtime() => new()
    {
        ["instance_id"] = Instance, ["target_id"] = "local-k3s", ["runtime_workload_id"] = "local-k3s/namespace",
        ["phase"] = "READY", ["endpoint_ready"] = true, ["metrics_available"] = false,
        ["observed_at"] = Now.ToString("O"),
        ["node"] = new JsonObject { ["ready"] = true, ["memory_pressure"] = false, ["disk_pressure"] = false, ["pid_pressure"] = false },
        ["containers"] = new JsonArray(new JsonObject { ["name"] = "smoke", ["pod_name"] = "smoke-1",
            ["state"] = "RUNNING", ["ready"] = true, ["restart_count"] = 0 }),
    };
    private static string Envelope(params JsonObject[] rows) => new JsonObject
    { ["code"] = "SUCCESS", ["data"] = new JsonObject { ["items"] = new JsonArray(rows.Select(row => (JsonNode)row).ToArray()), ["next_cursor"] = null } }.ToJsonString();
    private static HttpResponseMessage Json(string text, HttpStatusCode code = HttpStatusCode.OK) => new(code)
    { Content = new StringContent(text, Encoding.UTF8, "application/json") };

    [Fact]
    public async Task CompleteInventoryDoesNotImplyChallengeHealth()
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        var snapshot = lab.Inventory.Snapshot();
        var row = Assert.Single(snapshot.Instances);
        Assert.Equal("COMPLETE", snapshot.Status);
        Assert.True(snapshot.CompleteInventory);
        Assert.Equal("all_pages", snapshot.Coverage);
        Assert.Equal("READY", row.Status);
        Assert.Equal("UNVERIFIED", row.ChallengeHealth);
        Assert.Equal("UNMAPPED", row.Track);
        Assert.Equal("none", row.RecoveryAction);
        var json = JsonSerializer.Serialize(snapshot, PlatformJson.Options);
        Assert.Contains("\"instance_id\"", json);
        foreach (var secret in new[] { "secret-token", "untrusted.invalid", "service_url", "local-k3s/namespace" })
            Assert.DoesNotContain(secret, json);
        Assert.Equal(2, lab.Calls.Count);
    }

    [Theory]
    [InlineData("target")]
    [InlineData("vm")]
    [InlineData("not_scheduled")]
    public async Task MissingTargetBindingNeverFollowsWorkloadUrl(string fault)
    {
        var row = Row();
        if (fault == "target") row["runtime_target_id"] = "other-cluster";
        if (fault == "vm") row["runtime_type"] = "VM";
        if (fault == "not_scheduled") { row["runtime_target_id"] = null; row["status"] = "REQUESTED"; }
        using var lab = new Lab(Envelope(row));
        await lab.Inventory.RefreshAsync();
        Assert.Equal("UNVERIFIED", Assert.Single(lab.Inventory.Snapshot().Instances).Status);
        Assert.Single(lab.Calls);
    }

    [Theory]
    [InlineData("instance", "UNVERIFIED", "RUNTIME_IDENTITY_MISMATCH")]
    [InlineData("target", "UNVERIFIED", "RUNTIME_IDENTITY_MISMATCH")]
    [InlineData("workload", "UNVERIFIED", "RUNTIME_IDENTITY_MISMATCH")]
    [InlineData("stale", "STALE", "STALE_RUNTIME_OBSERVATION")]
    [InlineData("future", "STALE", "STALE_RUNTIME_OBSERVATION")]
    [InlineData("pressure", "DOWN", "K3S_NODE_PRESSURE")]
    [InlineData("containers", "DOWN", "RUNTIME_WORKLOAD_NOT_READY")]
    [InlineData("provisioning", "DOWN", "RUNTIME_WORKLOAD_NOT_READY")]
    [InlineData("unknown_phase", "UNVERIFIED", "INVALID_RUNTIME_PHASE")]
    [InlineData("malformed", "UNVERIFIED", "INVALID_RUNTIME_STATUS")]
    public async Task RuntimeMustBeFreshBoundAndReady(string fault, string status, string reason)
    {
        var runtime = Runtime();
        switch (fault)
        {
            case "instance": runtime["instance_id"] = Guid.NewGuid().ToString(); break;
            case "target": runtime["target_id"] = "wrong"; break;
            case "workload": runtime["runtime_workload_id"] = "different/namespace"; break;
            case "stale": runtime["observed_at"] = Now.AddMinutes(-2).ToString("O"); break;
            case "future": runtime["observed_at"] = Now.AddMinutes(2).ToString("O"); break;
            case "pressure": runtime["node"]!["memory_pressure"] = true; break;
            case "containers": runtime["containers"] = new JsonArray(); break;
            case "provisioning": runtime["phase"] = "PROVISIONING"; break;
            case "unknown_phase": runtime["phase"] = "PENDING"; break;
            case "malformed": runtime["endpoint_ready"] = "true"; break;
        }
        using var lab = new Lab(runtime: runtime.ToJsonString());
        await lab.Inventory.RefreshAsync();
        var observed = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal(status, observed.Status);
        Assert.Equal(reason, observed.ReasonCode);
        Assert.Equal("manual_review", observed.RecoveryAction);
    }

    [Theory]
    [InlineData("expires_at")]
    [InlineData("hard_expires_at")]
    [InlineData("idle_expires_at")]
    public async Task EarliestExpiryFlagsAResourceStillPresent(string field)
    {
        var row = Row();
        row[field] = Now.AddSeconds(-1).ToString("O");
        using var lab = new Lab(Envelope(row));
        await lab.Inventory.RefreshAsync();
        var observed = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.True(observed.TtlExpired);
        Assert.Equal("TTL_EXPIRED_RESOURCE_PRESENT", observed.ReasonCode);
        Assert.Equal("manual_review", observed.RecoveryAction);
    }

    [Theory]
    [InlineData("CLEANED", "READY", "DOWN", "TERMINAL_RESOURCE_PRESENT")]
    [InlineData("FAILED", "READY", "DOWN", "TERMINAL_RESOURCE_PRESENT")]
    [InlineData("CLEANED", "TERMINATED", "REMOVED", "RUNTIME_DELETION_CONFIRMED")]
    [InlineData("RUNNING", "TERMINATED", "DOWN", "ACTIVE_WORKLOAD_TERMINATED")]
    public async Task TerminalStateRequiresRuntimeDeletionEvidence(string scheduler, string phase, string expected, string reason)
    {
        var row = Row(); row["status"] = scheduler;
        var runtime = Runtime(); runtime["phase"] = phase;
        if (phase == "TERMINATED") { runtime["node"] = null; runtime["containers"] = new JsonArray(); }
        using var lab = new Lab(Envelope(row), runtime.ToJsonString());
        await lab.Inventory.RefreshAsync();
        var observed = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal(expected, observed.Status);
        Assert.Equal(reason, observed.ReasonCode);
    }

    [Theory]
    [InlineData("duplicate")]
    [InlineData("over_limit")]
    [InlineData("camel_case")]
    [InlineData("idle_invalid")]
    [InlineData("expiry_missing")]
    [InlineData("unknown_status")]
    public async Task MalformedInventoryCannotSilentlyOmitRows(string fault)
    {
        var row = Row();
        if (fault == "camel_case") { row.Remove("instance_id"); row["instanceId"] = Instance; }
        if (fault == "idle_invalid") row["idle_expires_at"] = "invalid";
        if (fault == "expiry_missing") row.Remove("hard_expires_at");
        if (fault == "unknown_status") row["status"] = "SUCCESS";
        var rows = fault == "duplicate" ? new[] { row, Row() } : fault == "over_limit"
            ? Enumerable.Range(0, 201).Select(_ => Row()).ToArray() : [row];
        using var lab = new Lab(Envelope(rows));
        await lab.Inventory.RefreshAsync();
        Assert.Equal("INVALID_SCHEDULER_INVENTORY", lab.Inventory.Snapshot().ReasonCode);
        Assert.Empty(lab.Inventory.Snapshot().Instances);
        Assert.Single(lab.Calls);
    }

    [Fact]
    public async Task EmptyFinalPageConfirmsEmptyInventory()
    {
        using var lab = new Lab(Envelope());
        await lab.Inventory.RefreshAsync();
        Assert.Equal("COMPLETE", lab.Inventory.Snapshot().Status);
        Assert.Equal("INVENTORY_EMPTY", lab.Inventory.Snapshot().ReasonCode);
        Assert.True(lab.Inventory.Snapshot().CompleteInventory);
    }

    [Theory]
    [InlineData(401, "AUTH_REQUIRED")]
    [InlineData(404, "RUNTIME_OR_ENDPOINT_NOT_FOUND")]
    [InlineData(302, "REDIRECT_BLOCKED")]
    [InlineData(500, "UPSTREAM_UNAVAILABLE")]
    public async Task FailedRefreshClearsPreviouslyReadyRows(int code, string reason)
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        lab.SchedulerCode = (HttpStatusCode)code;
        await lab.Inventory.RefreshAsync();
        Assert.Equal("UNAVAILABLE", lab.Inventory.Snapshot().Status);
        Assert.Equal(reason, lab.Inventory.Snapshot().ReasonCode);
        Assert.Empty(lab.Inventory.Snapshot().Instances);
    }

    [Theory]
    [InlineData("{", "INVALID_JSON")]
    [InlineData("oversized", "RESPONSE_TOO_LARGE")]
    public async Task InvalidRuntimePayloadRemainsUnverified(string payload, string reason)
    {
        using var lab = new Lab(runtime: payload == "oversized" ? new string('x', 262145) : payload);
        await lab.Inventory.RefreshAsync();
        var row = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("UNVERIFIED", row.Status);
        Assert.Equal(reason, row.ReasonCode);
    }

    [Fact]
    public async Task SnapshotAgesOutWhenWorkerStopsRefreshing()
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        lab.Clock.Current = Now.AddMinutes(3);
        Assert.Equal("STALE", lab.Inventory.Snapshot().Status);
        Assert.Equal("STALE", Assert.Single(lab.Inventory.Snapshot().Instances).Status);
    }

    [Fact]
    public async Task RuntimeObservationCanAgeOutBeforeTheInventoryRefreshDeadline()
    {
        using var lab = new Lab();
        await lab.Inventory.RefreshAsync();
        lab.Clock.Current = Now.AddSeconds(61);
        Assert.Equal("COMPLETE", lab.Inventory.Snapshot().Status);
        Assert.Equal("STALE", Assert.Single(lab.Inventory.Snapshot().Instances).Status);
    }

    [Fact]
    public async Task MissingCredentialDoesNotSendAnUnauthenticatedRequest()
    {
        using var lab = new Lab();
        Environment.SetEnvironmentVariable(lab.TokenKey, null);
        await lab.Inventory.RefreshAsync();
        Assert.Equal("AUTH_TOKEN_NOT_CONFIGURED", lab.Inventory.Snapshot().ReasonCode);
        Assert.Empty(lab.Calls);
    }

    private sealed class TestClock : TimeProvider
    {
        public DateTimeOffset Current { get; set; } = Now;
        public override DateTimeOffset GetUtcNow() => Current;
    }

    private static LocalFunctionalCheck Check() => new()
    {
        CheckId = "local_calculator", InstanceId = Guid.Parse(Instance), ChallengeId = Guid.Parse(Challenge),
        TargetId = "local-k3s", RuntimeWorkloadId = "local-k3s/namespace", Url = "http://127.0.0.1:30080/calculate",
    };

    [Theory]
    [InlineData("ok", "UP", "READY")]
    [InlineData("wrong", "DOWN", "DOWN")]
    [InlineData("unavailable", "DOWN", "DOWN")]
    [InlineData("json", "CHECKER_ERROR", "UNVERIFIED")]
    [InlineData("nonce", "CHECKER_ERROR", "UNVERIFIED")]
    [InlineData("redirect", "CHECKER_ERROR", "UNVERIFIED")]
    [InlineData("oversize", "CHECKER_ERROR", "UNVERIFIED")]
    [InlineData("type", "CHECKER_ERROR", "UNVERIFIED")]
    public async Task FunctionalResultIsSeparateFromRuntimeReadiness(string fault, string health, string status)
    {
        using var lab = new Lab(functional: Check(), function: request =>
        {
            var nonce = request.RequestUri!.Query.Split("nonce=")[1];
            Assert.Matches("^[a-f0-9]{32}$", nonce);
            if (fault == "json") return Json("{");
            if (fault == "redirect") return Json("{}", HttpStatusCode.Redirect);
            if (fault == "unavailable") return Json("{}", HttpStatusCode.ServiceUnavailable);
            if (fault == "oversize") return Json(new string('x', 8193));
            if (fault == "type") return new(HttpStatusCode.OK) { Content = new StringContent("42") };
            return Json(JsonSerializer.Serialize(new { service = "sentinel-functional-fixture-v1",
                nonce = fault == "nonce" ? "old-response" : nonce, result = fault == "wrong" ? 43 : 42 }));
        });
        await lab.Inventory.RefreshAsync();
        var row = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("READY", row.RuntimePhase);
        Assert.Equal(health, row.ChallengeHealth);
        Assert.Equal(status, row.Status);
        Assert.Equal("local_functional_fixture", row.FunctionalCheck!.Scope);
        Assert.Equal("UNMAPPED", row.Track);
        Assert.Equal(3, lab.Calls.Count);
        var json = JsonSerializer.Serialize(row, PlatformJson.Options);
        Assert.DoesNotContain("127.0.0.1", json);
        Assert.DoesNotContain("secret-token", json);
        Assert.DoesNotContain("namespace", json);
    }

    [Theory]
    [InlineData("challenge")]
    [InlineData("workload")]
    [InlineData("target")]
    [InlineData("missing_challenge")]
    public async Task WrongFunctionalBindingPreventsWorkloadRequests(string field)
    {
        var check = Check(); var scheduler = Row();
        if (field == "challenge") check.ChallengeId = Guid.NewGuid();
        if (field == "workload") check.RuntimeWorkloadId = "different/namespace";
        if (field == "target") check.TargetId = "other-cluster";
        if (field == "missing_challenge") scheduler.Remove("challenge_id");
        using var lab = new Lab(Envelope(scheduler), functional: check);
        await lab.Inventory.RefreshAsync();
        var row = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("FUNCTIONAL_BINDING_MISMATCH", row.ReasonCode);
        Assert.Equal("UNVERIFIED", row.ChallengeHealth);
        Assert.Equal(2, lab.Calls.Count);
    }

    [Theory]
    [InlineData(61)]
    [InlineData(105)]
    public async Task FunctionalPassExpiresWithItsRuntimeOrInventoryObservation(int seconds)
    {
        using var lab = new Lab(functional: Check(), function: request => Json(JsonSerializer.Serialize(new
        { service = "sentinel-functional-fixture-v1", nonce = request.RequestUri!.Query.Split("nonce=")[1], result = 42 })));
        await lab.Inventory.RefreshAsync();
        lab.Clock.Current = Now.AddSeconds(seconds);
        var row = Assert.Single(lab.Inventory.Snapshot().Instances);
        Assert.Equal("STALE", row.Status);
        Assert.Equal("STALE", row.ChallengeHealth);
        Assert.Equal("STALE", row.FunctionalCheck!.Status);
    }

    [Theory]
    [InlineData("http://external.invalid:30080/calculate")]
    [InlineData("http://127.0.0.1:30079/calculate")]
    [InlineData("http://host.docker.internal:30080/admin")]
    [InlineData("http://user:secret@127.0.0.1:30080/calculate")]
    [InlineData("http://127.0.0.1:30080/calculate?redirect=1")]
    public void FunctionalUrlsAreRestrictedToExplicitLocalFixturePorts(string url)
    {
        var check = Check(); check.Url = url;
        Assert.False(check.IsValid());
    }

    [Fact]
    public async Task ExpiredInstanceIsNeverFunctionallyProbed()
    {
        var row = Row(); row["expires_at"] = Now.AddSeconds(-1).ToString("O");
        using var lab = new Lab(Envelope(row), functional: Check());
        await lab.Inventory.RefreshAsync();
        Assert.Equal("TTL_EXPIRED_RESOURCE_PRESENT", Assert.Single(lab.Inventory.Snapshot().Instances).ReasonCode);
        Assert.Equal(2, lab.Calls.Count);
    }

    [Fact]
    public async Task UnresponsiveFunctionHasABoundedDeadline()
    {
        var key = "FUNCTION_TIMEOUT_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        Environment.SetEnvironmentVariable(key, "secret-token");
        try
        {
            using var client = new HttpClient(new AsyncHandler(async (request, token) =>
            {
                if (request.RequestUri!.Host == "scheduler.local") return Json(Envelope(Row()));
                if (request.RequestUri.Host == "runtime.local") return Json(Runtime().ToJsonString());
                Assert.Null(request.Headers.Authorization);
                await Task.Delay(Timeout.InfiniteTimeSpan, token);
                throw new InvalidOperationException("Deadline was not applied");
            }));
            var inventory = new InstanceInventory(client, Options.Create(new InstanceInventoryOptions
            {
                Enabled = true, TimeoutSeconds = 1, SchedulerTokenEnv = key, SchedulerUrl = "http://scheduler.local/api/instances",
                RuntimeTargets = [new() { TargetId = "local-k3s", BaseUrl = "http://runtime.local", AuthTokenEnv = key }],
                FunctionalChecks = [Check()],
            }), new TestClock());
            await inventory.RefreshAsync().WaitAsync(TimeSpan.FromSeconds(5));
            var row = Assert.Single(inventory.Snapshot().Instances);
            Assert.Equal("READY", row.RuntimePhase);
            Assert.Equal("DOWN", row.ChallengeHealth);
            Assert.Equal("FUNCTION_TIMEOUT", row.FunctionalCheck!.ReasonCode);
        }
        finally { Environment.SetEnvironmentVariable(key, null); }
    }

    [Fact]
    public async Task RuntimeTimeoutDoesNotDiscardOtherInstanceObservations()
    {
        var key = "INSTANCE_TIMEOUT_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        Environment.SetEnvironmentVariable(key, "secret-token");
        try
        {
            using var client = new HttpClient(new AsyncHandler(async (request, token) =>
            {
                if (request.RequestUri!.Host == "scheduler.local") return Json(Envelope(Row()));
                await Task.Delay(Timeout.InfiniteTimeSpan, token);
                throw new InvalidOperationException("Deadline was not applied");
            }));
            var inventory = new InstanceInventory(client, Options.Create(new InstanceInventoryOptions
            {
                Enabled = true, TimeoutSeconds = 1, SchedulerTokenEnv = key, SchedulerUrl = "http://scheduler.local/api/instances",
                RuntimeTargets = [new() { TargetId = "local-k3s", BaseUrl = "http://runtime.local", AuthTokenEnv = key }],
            }), new TestClock());
            await inventory.RefreshAsync().WaitAsync(TimeSpan.FromSeconds(5));
            Assert.Equal("COMPLETE", inventory.Snapshot().Status);
            Assert.Equal("UPSTREAM_TIMEOUT", Assert.Single(inventory.Snapshot().Instances).ReasonCode);
        }
        finally { Environment.SetEnvironmentVariable(key, null); }
    }

    [Fact]
    public async Task FullPageUsesAtMostFourConcurrentRuntimeRequests()
    {
        var key = "INSTANCE_CONCURRENCY_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        Environment.SetEnvironmentVariable(key, "secret-token");
        try
        {
            var rows = Enumerable.Range(0, 200).Select(_ => { var row = Row(); row["instance_id"] = Guid.NewGuid().ToString(); return row; }).ToArray();
            var active = 0; var maximum = 0; var gate = new object();
            using var client = new HttpClient(new AsyncHandler(async (request, token) =>
            {
                if (request.RequestUri!.Host == "scheduler.local") return Json(Envelope(rows));
                lock (gate) { active++; maximum = Math.Max(maximum, active); }
                try
                {
                    await Task.Delay(30, token);
                    var runtime = Runtime(); runtime["instance_id"] = request.RequestUri.Segments[^2].TrimEnd('/');
                    return Json(runtime.ToJsonString());
                }
                finally { lock (gate) active--; }
            }));
            var inventory = new InstanceInventory(client, Options.Create(new InstanceInventoryOptions
            {
                Enabled = true, SchedulerTokenEnv = key, SchedulerUrl = "http://scheduler.local/api/instances",
                RuntimeTargets = [new() { TargetId = "local-k3s", BaseUrl = "http://runtime.local", AuthTokenEnv = key }],
            }), new TestClock());
            await inventory.RefreshAsync();
            Assert.Equal(200, inventory.Snapshot().Instances.Count);
            Assert.All(inventory.Snapshot().Instances, row => Assert.Equal("READY", row.Status));
            Assert.InRange(maximum, 2, 4);
        }
        finally { Environment.SetEnvironmentVariable(key, null); }
    }

    private sealed class AsyncHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> response) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => response(request, cancellationToken);
    }
    private sealed class Lab : IDisposable
    {
        public string TokenKey { get; } = "INSTANCE_TEST_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        public TestClock Clock { get; } = new();
        public List<Uri> Calls { get; } = [];
        public HttpStatusCode SchedulerCode { get; set; } = HttpStatusCode.OK;
        public InstanceInventory Inventory { get; }
        private readonly HttpClient _client;
        public Lab(string? scheduler = null, string? runtime = null, LocalFunctionalCheck? functional = null,
            Func<HttpRequestMessage, HttpResponseMessage>? function = null)
        {
            Environment.SetEnvironmentVariable(TokenKey, "secret-token");
            _client = new HttpClient(new Handler(request =>
            {
                Assert.Equal(HttpMethod.Get, request.Method);
                var uri = request.RequestUri!;
                Calls.Add(uri);
                if (uri.Host == "127.0.0.1")
                {
                    Assert.Null(request.Headers.Authorization);
                    Assert.False(request.Headers.Contains("Cookie"));
                    Assert.Equal("/calculate", uri.AbsolutePath);
                    Assert.NotNull(function);
                    return function(request);
                }
                Assert.Equal("secret-token", request.Headers.Authorization?.Parameter);
                Assert.Contains(uri.Host, new[] { "scheduler.local", "runtime.local" });
                if (uri.Host == "scheduler.local") return Json(scheduler ?? Envelope(Row()), SchedulerCode);
                Assert.Equal("/internal/v1/instances/" + Instance + "/runtime-status", uri.AbsolutePath);
                return Json(runtime ?? Runtime().ToJsonString());
            }));
            Inventory = new(_client, Options.Create(new InstanceInventoryOptions
            {
                Enabled = true, SchedulerUrl = "http://scheduler.local/api/instances", SchedulerTokenEnv = TokenKey,
                RuntimeTargets = [new() { TargetId = "local-k3s", BaseUrl = "http://runtime.local", AuthTokenEnv = TokenKey }],
                FunctionalChecks = functional is null ? [] : [functional],
            }), Clock);
        }
        public void Dispose() { _client.Dispose(); Environment.SetEnvironmentVariable(TokenKey, null); }
    }
    private sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> response) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(response(request));
    }
}
