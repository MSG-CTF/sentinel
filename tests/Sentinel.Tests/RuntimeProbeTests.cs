using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class RuntimeProbeTests
{
    private const string Instance = "11111111-1111-4111-8111-111111111111";
    private static JsonObject Sample() => new()
    {
        ["instance_id"] = Instance, ["target_id"] = "local-k3s", ["runtime_workload_id"] = "local-k3s/namespace",
        ["phase"] = "READY", ["endpoint_ready"] = true, ["metrics_available"] = false,
        ["observed_at"] = DateTimeOffset.UtcNow.ToString("O"),
        ["node"] = new JsonObject { ["ready"] = true, ["memory_pressure"] = false, ["disk_pressure"] = false, ["pid_pressure"] = false },
        ["containers"] = new JsonArray(new JsonObject { ["name"] = "smoke", ["pod_name"] = "smoke-123", ["state"] = "RUNNING", ["ready"] = true, ["restart_count"] = 0 }),
    };

    [Fact]
    public async Task RealWorkloadMayBeReadyWithoutMetrics()
    {
        var observation = await Check(Sample());
        Assert.Equal("HEALTHY", observation.Status);
        Assert.Equal("RUNTIME_WORKLOAD_READY", observation.ReasonCode);
        Assert.Equal("no", observation.Components["metrics_available"]);
        Assert.DoesNotContain("secret-runtime", JsonSerializer.Serialize(observation, PlatformJson.Options));
    }

    [Theory]
    [InlineData("identity", "UNVERIFIED", "RUNTIME_IDENTITY_MISMATCH")]
    [InlineData("stale", "UNVERIFIED", "STALE_RUNTIME_OBSERVATION")]
    [InlineData("future", "UNVERIFIED", "STALE_RUNTIME_OBSERVATION")]
    [InlineData("node", "DOWN", "K3S_NODE_NOT_READY")]
    [InlineData("pressure", "DOWN", "K3S_NODE_PRESSURE")]
    [InlineData("empty", "DOWN", "RUNTIME_WORKLOAD_NOT_READY")]
    [InlineData("pod", "DOWN", "RUNTIME_WORKLOAD_NOT_READY")]
    [InlineData("endpoint", "DOWN", "RUNTIME_WORKLOAD_NOT_READY")]
    [InlineData("malformed", "UNVERIFIED", "INVALID_RUNTIME_STATUS")]
    public async Task InvalidOrFailedWorkloadsCannotBeHealthy(string fault, string status, string reason)
    {
        var body = Sample();
        switch (fault)
        {
            case "identity": body["target_id"] = "other-cluster"; break;
            case "stale": body["observed_at"] = DateTimeOffset.UtcNow.AddMinutes(-3).ToString("O"); break;
            case "future": body["observed_at"] = DateTimeOffset.UtcNow.AddMinutes(3).ToString("O"); break;
            case "node": body["node"]!["ready"] = false; break;
            case "pressure": body["node"]!["disk_pressure"] = true; break;
            case "empty": body["containers"] = new JsonArray(); break;
            case "pod": body["containers"]![0]!["state"] = "WAITING"; break;
            case "endpoint": body["endpoint_ready"] = false; break;
            case "malformed": body["containers"]![0]!["restart_count"] = "0"; break;
        }
        var observation = await Check(body);
        Assert.Equal(status, observation.Status);
        Assert.Equal(reason, observation.ReasonCode);
    }

    [Fact]
    public async Task NodeReadinessDoesNotClaimWorkloadReadiness()
    {
        var body = Sample();
        body["endpoint_ready"] = false;
        body["containers"] = new JsonArray();
        var observation = await Check(body, "runtime_node");
        Assert.Equal("K3S_NODE_READY", observation.ReasonCode);
        Assert.Equal("down", observation.Components["endpoint_ready"]);
    }

    [Theory]
    [InlineData("SUCCEEDED", "operation-local", "HEALTHY")]
    [InlineData("RUNNING", "operation-local", "UNVERIFIED")]
    [InlineData("SUCCEEDED", "wrong-operation", "UNVERIFIED")]
    public async Task StoreRequiresExpectedCompletedOperation(string state, string id, string expected)
    {
        var body = new JsonObject { ["operation_id"] = id, ["type"] = "CREATE", ["status"] = state,
            ["attempt"] = 1, ["max_attempts"] = 4, ["result"] = new JsonObject { ["runtime_workload_id"] = "local/ns" } };
        var observation = await Check(body, "runtime_operation");
        Assert.Equal(expected, observation.Status);
    }

    private static async Task<ProbeObservation> Check(JsonObject body, string kind = "runtime_status")
    {
        var tokenKey = "RUNTIME_TEST_" + Guid.NewGuid().ToString("N");
        Environment.SetEnvironmentVariable(tokenKey, "secret-runtime");
        try
        {
            return await new PlatformProbeClient(new HttpClient(new Handler(body.ToJsonString()))).CheckAsync(new PlatformTarget
            {
                TargetId = "runtime", Name = "Runtime", Role = "RUNTIME", Track = "RUNTIME", Kind = kind,
                Url = "http://localhost/internal/v1/instances/" + Instance + "/runtime-status", AuthTokenEnv = tokenKey,
                ExpectedFields = kind == "runtime_operation" ? new() { ["operation_id"] = "operation-local" }
                    : new() { ["instance_id"] = Instance, ["target_id"] = "local-k3s" },
            }, 2);
        }
        finally { Environment.SetEnvironmentVariable(tokenKey, null); }
    }

    private sealed class Handler(string body) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("Bearer", request.Headers.Authorization?.Scheme);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") });
        }
    }
}
