using System.Net;
using System.Text;
using System.Text.Json;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class InventoryProbeTests
{
    [Theory]
    [InlineData("scheduler_inventory", "{\"code\":\"SUCCESS\",\"data\":{\"items\":[],\"next_cursor\":null}}", "SCHEDULER_QUERY_READY")]
    [InlineData("broker_inventory", "{\"total\":0,\"limit\":100,\"offset\":0,\"items\":[]}", "BROKER_QUERY_READY")]
    public async Task EmptyAuthenticatedInventoryProvesQueryOnly(string kind, string body, string reason)
    {
        var (result, requests) = await Check(kind, body);
        Assert.Equal("HEALTHY", result.Status);
        Assert.Equal(reason, result.ReasonCode);
        Assert.Equal("query_only", result.EvidenceScope);
        Assert.Equal(1, requests);
        Assert.DoesNotContain("secret-value", JsonSerializer.Serialize(result, PlatformJson.Options));
    }

    [Theory]
    [InlineData("scheduler_inventory", "{\"code\":\"SUCCESS\",\"data\":{}}")]
    [InlineData("scheduler_inventory", "{\"code\":\"SUCCESS\",\"data\":[true]}")]
    [InlineData("scheduler_inventory", "{\"code\":\"ERROR\",\"data\":[]}")]
    [InlineData("broker_inventory", "{\"total\":2,\"limit\":100,\"offset\":0,\"items\":[]}")]
    [InlineData("broker_inventory", "{\"total\":0,\"limit\":100,\"offset\":1,\"items\":[]}")]
    [InlineData("broker_inventory", "{\"total\":0,\"limit\":0,\"offset\":0,\"items\":[]}")]
    [InlineData("broker_inventory", "{\"total\":\"0\",\"limit\":100,\"offset\":0,\"items\":[]}")]
    [InlineData("broker_inventory", "{\"total\":0,\"limit\":100,\"offset\":0,\"items\":[],\"generated_at\":\"2000-01-01T00:00:00Z\"}")]
    public async Task MisleadingInventoryCannotBeHealthy(string kind, string body)
    {
        var (result, _) = await Check(kind, body);
        Assert.Equal("UNVERIFIED", result.Status);
        Assert.Equal("INVALID_INVENTORY_CONTRACT", result.ReasonCode);
    }

    [Fact]
    public async Task PartialBrokerPageNeverExposesOrClaimsWholeInventory()
    {
        var (result, _) = await Check("broker_inventory", "{\"total\":2,\"limit\":1,\"offset\":0,\"items\":[{\"account_id\":\"private-account\"}]}");
        Assert.Equal("HEALTHY", result.Status);
        Assert.Equal("2", result.Components["inventory_total"]);
        Assert.Equal("1", result.Components["page_count"]);
        Assert.DoesNotContain("private-account", JsonSerializer.Serialize(result, PlatformJson.Options));
    }

    [Fact]
    public async Task MissingCredentialDoesNotSendRequest()
    {
        var (result, requests) = await Check("broker_inventory", "{}", token: false);
        Assert.Equal("AUTH_TOKEN_NOT_CONFIGURED", result.ReasonCode);
        Assert.Equal(0, requests);
    }

    [Fact]
    public async Task AbsentClusterIsNotProbedOrReportedAsAnOutage()
    {
        var handler = new Handler();
        var result = await new PlatformProbeClient(new HttpClient(handler)).CheckAsync(new PlatformTarget
        { TargetId="runtime", Name="Runtime", Role="RUNTIME", Track="RUNTIME", Enabled=false,
          UnconfiguredReason="CLUSTER_NOT_CONFIGURED", EvidenceScope="kubeconfig required" }, 2);
        Assert.Equal("UNCONFIGURED", result.Status);
        Assert.Equal("CLUSTER_NOT_CONFIGURED", result.ReasonCode);
        Assert.Equal("kubeconfig required", result.EvidenceScope);
        Assert.Null(result.CheckedAt);
        Assert.Equal(0, handler.Requests);
    }

    private static async Task<(ProbeObservation, int)> Check(string kind, string body, bool token = true)
    {
        if (kind == "broker_inventory" && !body.Contains("generated_at"))
            body = body[..^1] + ",\"generated_at\":\"" + DateTimeOffset.UtcNow.ToString("O") + "\"}";
        var key = "SENTINEL_TEST_" + Guid.NewGuid().ToString("N");
        if (token) Environment.SetEnvironmentVariable(key, "secret-value");
        try
        {
            var handler = new Handler { Body = body };
            var target = new PlatformTarget { TargetId="control", Name="Control", Role="CONTROL", Track="CONTROL",
                Kind=kind, Url=kind == "scheduler_inventory" ? "http://localhost/api/instances" : "http://localhost/inventory", AuthTokenEnv=key, EvidenceScope="query_only" };
            var result = await new PlatformProbeClient(new HttpClient(handler)).CheckAsync(target, 2);
            return (result, handler.Requests);
        }
        finally { Environment.SetEnvironmentVariable(key, null); }
    }

    private sealed class Handler : HttpMessageHandler
    {
        public string Body { get; init; } = "{}";
        public int Requests { get; private set; }
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Requests++;
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("Bearer", request.Headers.Authorization?.Scheme);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                { Content = new StringContent(Body, Encoding.UTF8, "application/json") });
        }
    }
}
