using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

[Collection("Discord environment")]
public sealed class RuntimeReadinessTests : IDisposable
{
    private const string TokenName = "RUNTIME_READINESS_TEST_TOKEN";
    private const string Token = "runtime-readiness-test-only-credential";
    private readonly string? _oldToken = Environment.GetEnvironmentVariable(TokenName);
    private readonly string? _oldWebhook = Environment.GetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL");
    private readonly string _file = Path.Combine(Path.GetTempPath(), "sentinel-runtime-readiness-" + Guid.NewGuid());

    public RuntimeReadinessTests()
    {
        Environment.SetEnvironmentVariable(TokenName, Token);
        Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL",
            "https://discord.com/api/webhooks/12345678901234567/" + new string('a', 40));
    }

    private static PlatformTarget Target() => new()
    {
        TargetId = "runtime-api", Name = "런타임 API·저장소", Role = "RUNTIME", Track = "RUNTIME",
        Kind = "runtime_healthz", Url = "https://runtime.invalid/internal/v1/healthz", AuthTokenEnv = TokenName,
    };

    private static PlatformProbeClient Client(Handler handler) => new(new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan });

    [Theory]
    [InlineData(200, "ok", "postgres", "ok", "primary", "HEALTHY", "RUNTIME_API_READY")]
    [InlineData(200, "ok", "postgres", "ok", "deployment_bridge", "HEALTHY", "RUNTIME_API_READY")]
    [InlineData(503, "down", "postgres", "down", "primary", "DOWN", "RUNTIME_DATABASE_DOWN")]
    [InlineData(503, "down", "memory", "not_configured", "primary", "DOWN", "RUNTIME_STORE_NOT_DURABLE")]
    [InlineData(200, "down", "postgres", "down", "primary", "UNVERIFIED", "INCONSISTENT_RUNTIME_READINESS")]
    [InlineData(503, "ok", "postgres", "ok", "primary", "DOWN", "HTTP_UNAVAILABLE")]
    [InlineData(200, "ok", "memory", "ok", "primary", "UNVERIFIED", "INCONSISTENT_RUNTIME_READINESS")]
    [InlineData(200, "ok", "postgres", "not_configured", "primary", "UNVERIFIED", "INCONSISTENT_RUNTIME_READINESS")]
    [InlineData(200, "ok", "postgres", "ok", "unknown", "UNVERIFIED", "INVALID_RUNTIME_READINESS")]
    public async Task DurableStoreContractAndBridgeModeAreRequired(int code, string status, string store, string database,
        string mode, string expected, string reason)
    {
        var timestamp = DateTimeOffset.UtcNow;
        var handler = new Handler(_ => new((HttpStatusCode)code)
        { Content = JsonContent.Create(new { status, store_mode = store, database, mode, checked_at = timestamp }) });
        var result = await Client(handler).CheckAsync(Target(), 2);
        Assert.Equal(expected, result.Status);
        Assert.Equal(reason, result.ReasonCode);
        if (reason is "RUNTIME_API_READY" or "RUNTIME_DATABASE_DOWN" or "RUNTIME_STORE_NOT_DURABLE")
        {
            Assert.Equal(timestamp, result.CheckedAt);
            Assert.Equal(database, result.Components["database"]);
        }
        else Assert.Empty(result.Components);
    }

    [Theory]
    [InlineData(-31)]
    [InlineData(10)]
    public async Task CachedOrFutureStateCannotBeHealthy(int seconds)
    {
        var handler = new Handler(_ => Healthy(DateTimeOffset.UtcNow.AddSeconds(seconds)));
        var result = await Client(handler).CheckAsync(Target(), 2);
        Assert.Equal("UNVERIFIED", result.Status);
        Assert.Equal("STALE_RUNTIME_READINESS", result.ReasonCode);
        Assert.Empty(result.Components);
    }

    [Theory]
    [InlineData(401, "UNVERIFIED", "AUTH_REQUIRED")]
    [InlineData(403, "UNVERIFIED", "AUTH_REQUIRED")]
    [InlineData(404, "UNVERIFIED", "ENDPOINT_NOT_FOUND")]
    [InlineData(302, "UNVERIFIED", "REDIRECT_BLOCKED")]
    [InlineData(429, "DEGRADED", "HTTP_RATE_LIMITED")]
    [InlineData(502, "DOWN", "HTTP_ERROR")]
    public async Task ErrorCodesDoNotTurnIntoReadiness(int code, string status, string reason)
    {
        var result = await Client(new Handler(_ => new((HttpStatusCode)code))).CheckAsync(Target(), 2);
        Assert.Equal(status, result.Status);
        Assert.Equal(reason, result.ReasonCode);
        Assert.Empty(result.Components);
    }

    [Theory]
    [InlineData(200, "text/html", "<html>proxy</html>", "UNVERIFIED", "EXPECTED_JSON_RECEIVED_OTHER")]
    [InlineData(200, "application/json", "{bad", "UNVERIFIED", "INVALID_JSON")]
    [InlineData(200, "application/json", "{}", "UNVERIFIED", "INVALID_RUNTIME_READINESS")]
    [InlineData(200, "application/json", "[]", "UNVERIFIED", "INVALID_HEALTH_CONTRACT")]
    [InlineData(503, "text/html", "<html>proxy</html>", "DOWN", "HTTP_UNAVAILABLE")]
    [InlineData(503, "application/json", "{bad", "DOWN", "HTTP_UNAVAILABLE")]
    public async Task PartialOrProxyPayloadsCannotInventDatabaseState(int code, string media, string body, string status, string reason)
    {
        var result = await Client(new Handler(_ => new((HttpStatusCode)code)
        { Content = new StringContent(body, Encoding.UTF8, media) })).CheckAsync(Target(), 2);
        Assert.Equal(status, result.Status);
        Assert.Equal(reason, result.ReasonCode);
        Assert.Empty(result.Components);
    }

    [Fact]
    public async Task DuplicateHealthFieldsAndMissingCredentialsAreRejected()
    {
        var body = "{\"status\":\"down\",\"status\":\"ok\",\"store_mode\":\"postgres\",\"database\":\"ok\",\"mode\":\"primary\",\"checked_at\":\"" + DateTimeOffset.UtcNow.ToString("O") + "\"}";
        var handler = new Handler(_ => new(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") });
        Assert.Equal("INVALID_RUNTIME_READINESS", (await Client(handler).CheckAsync(Target(), 2)).ReasonCode);
        Environment.SetEnvironmentVariable(TokenName, null);
        handler.Calls = 0;
        Assert.Equal("AUTH_TOKEN_NOT_CONFIGURED", (await Client(handler).CheckAsync(Target(), 2)).ReasonCode);
        Assert.Equal(0, handler.Calls);
    }

    [Theory]
    [InlineData("http://runtime.invalid/internal/v1/healthz", TokenName)]
    [InlineData("https://runtime.invalid/internal/v1/operations/missing", TokenName)]
    [InlineData("https://runtime.invalid/internal/v1/healthz", "")]
    [InlineData("https://runtime.invalid/internal/v1/healthz?token=secret", TokenName)]
    public void MonitoringRequiresAuthenticatedHttpsAtTheExactEndpoint(string url, string token)
    {
        var target = Target(); target.Url = url; target.AuthTokenEnv = token;
        Assert.False(target.IsValid());
    }

    [Fact]
    public async Task DatabaseIncidentSurvivesRestartWithoutRepeatsAndNeedsTwoNewHealthyMeasurements()
    {
        var clock = new Clock();
        var timestamp = clock.Now;
        var down = true;
        var handler = new Handler(_ => down
            ? new(HttpStatusCode.ServiceUnavailable) { Content = JsonContent.Create(new
                { status = "down", store_mode = "postgres", database = "down", mode = "primary", checked_at = timestamp }) }
            : Healthy(timestamp));
        var webhook = new Webhook();
        var options = Options.Create(new PlatformOptions { Targets = [Target()], EventFile = _file + ".events" });
        var monitor = new PlatformMonitor(options, new PlatformProbeClient(new HttpClient(handler), timeProvider: clock),
            NullLogger<PlatformMonitor>.Instance, clock);
        DiscordAlerts Alerts() => new(Options.Create(new DiscordAlertOptions { Enabled = true, StateFile = _file }), new HttpClient(webhook), clock);
        var alerts = Alerts();
        async Task Check()
        {
            await monitor.RefreshAsync(); alerts.Observe(monitor.Snapshot()); await alerts.DeliverAsync();
        }
        await Check();
        Assert.Single(webhook.Messages);
        Assert.Contains("RUNTIME_DATABASE_DOWN", webhook.Messages[0].GetRawText());
        Assert.DoesNotContain(Token, webhook.Messages[0].GetRawText());
        alerts = Alerts();
        for (var index = 0; index < 70; index++)
        {
            clock.Now = clock.Now.AddSeconds(15); timestamp = clock.Now;
            await Check();
        }
        Assert.Single(webhook.Messages);
        Assert.Single(monitor.Events());
        down = false;
        clock.Now = clock.Now.AddSeconds(15); timestamp = clock.Now; await Check();
        await Check(); // The same cached measurement cannot count as a second recovery sample.
        Assert.Single(webhook.Messages);
        Assert.Single(monitor.Events());
        clock.Now = clock.Now.AddSeconds(15); timestamp = clock.Now; await Check();
        Assert.Equal(2, webhook.Messages.Count);
        Assert.Equal(2, monitor.Events().Count);
        Assert.Equal("복구 확인 · 런타임 API·저장소", webhook.Messages[1].GetProperty("embeds")[0].GetProperty("title").GetString());
    }

    private static HttpResponseMessage Healthy(DateTimeOffset at) => new(HttpStatusCode.OK)
    { Content = JsonContent.Create(new { status = "ok", store_mode = "postgres", database = "ok", mode = "primary", checked_at = at }) };

    private sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> reply) : HttpMessageHandler
    {
        public int Calls;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Calls++;
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("runtime.invalid", request.RequestUri!.Host);
            Assert.Equal("Bearer", request.Headers.Authorization?.Scheme);
            Assert.Equal(Token, request.Headers.Authorization?.Parameter);
            Assert.True(request.Headers.CacheControl?.NoCache);
            Assert.True(request.Headers.CacheControl?.NoStore);
            return Task.FromResult(reply(request));
        }
    }

    private sealed class Webhook : HttpMessageHandler
    {
        public List<JsonElement> Messages = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            Messages.Add(body.RootElement.Clone());
            return new(HttpStatusCode.NoContent);
        }
    }

    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }

    public void Dispose()
    {
        Environment.SetEnvironmentVariable(TokenName, _oldToken);
        Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL", _oldWebhook);
        foreach (var file in new[] { _file, _file + ".events" }) if (File.Exists(file)) File.Delete(file);
    }
}
