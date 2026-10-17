using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class PlatformProbeTests
{
    [Fact]
    public async Task ActuatorNegotiatesJsonInsteadOfVendorMediaType()
    {
        var handler = new Handler(request =>
        {
            Assert.Contains(request.Headers.Accept, accept => accept.MediaType == "application/json");
            return new(HttpStatusCode.OK) { Content = JsonContent.Create(new { status = "UP" }) };
        });
        var target = Target(); target.Kind = "json_fields";
        target.ExpectedFields = new Dictionary<string, string> { ["status"] = "UP" };
        Assert.Equal("HEALTHY", (await Client(handler).CheckAsync(target, 2)).Status);
    }

    [Theory]
    [InlineData(200, "ok", "ok", "ok", "HEALTHY", "BACKEND_READY")]
    [InlineData(503, "down", "down", "ok", "DOWN", "DATABASE_DOWN")]
    [InlineData(503, "down", "ok", "down", "DOWN", "CACHE_DOWN")]
    [InlineData(503, "down", "down", "down", "DOWN", "DATABASE_AND_CACHE_DOWN")]
    [InlineData(200, "down", "ok", "down", "UNVERIFIED", "INCONSISTENT_HEALTH_CONTRACT")]
    [InlineData(503, "ok", "ok", "ok", "DOWN", "HTTP_UNAVAILABLE")]
    [InlineData(200, "healthy", "healthy", "healthy", "UNVERIFIED", "INVALID_HEALTH_CONTRACT")]
    public async Task ActualBackendContractIsRequired(int code, string status, string database, string cache,
        string expectedStatus, string reason)
    {
        var handler = new Handler(_ => new((HttpStatusCode)code)
        { Content = JsonContent.Create(new { status, database, cache }) });
        var result = await Client(handler).CheckAsync(Target(), 2);
        Assert.Equal(expectedStatus, result.Status);
        Assert.Equal(reason, result.ReasonCode);
        Assert.All(handler.Methods, method => Assert.Equal(HttpMethod.Get, method));
    }

    [Theory]
    [InlineData("text/html", "<html>MSG CTF 2026</html>", "EXPECTED_JSON_RECEIVED_OTHER")]
    [InlineData("application/json", "{}", "INVALID_HEALTH_CONTRACT")]
    [InlineData("application/json", "[]", "INVALID_HEALTH_CONTRACT")]
    [InlineData("application/json", "{oops", "INVALID_JSON")]
    [InlineData("application/json", "{\"status\":\"ok\",\"database\":\"ok\",\"cache\":true}", "INVALID_HEALTH_CONTRACT")]
    [InlineData("application/json", "{\"Status\":\"ok\",\"Database\":\"ok\",\"Cache\":\"ok\"}", "INVALID_HEALTH_CONTRACT")]
    public async Task HtmlAndMalformedPayloadCannotBecomeHealthy(string mediaType, string body, string reason)
    {
        var result = await Client(new Handler(_ => new(HttpStatusCode.OK)
        { Content = new StringContent(body, Encoding.UTF8, mediaType) })).CheckAsync(Target(), 2);
        Assert.Equal("UNVERIFIED", result.Status);
        Assert.Equal(reason, result.ReasonCode);
    }

    [Theory]
    [InlineData("backend_healthz")]
    [InlineData("auth_boundary")]
    [InlineData("http")]
    [InlineData("json_fields")]
    public async Task RateLimitingIsAWarningWithoutInventingHostOverload(string kind)
    {
        var target = Target(); target.Kind = kind;
        target.ExpectedFields = new() { ["status"] = "UP" };
        var result = await Client(new Handler(_ => new(HttpStatusCode.TooManyRequests)
        { Content = new StringContent("rate limited") })).CheckAsync(target, 2);
        Assert.Equal("DEGRADED", result.Status);
        Assert.Equal("HTTP_RATE_LIMITED", result.ReasonCode);
        Assert.Equal(429, result.HttpStatusCode);
        Assert.Empty(result.Components);
    }

    [Theory]
    [InlineData("text/html", "<html>proxy unavailable</html>")]
    [InlineData("application/json", "{oops")]
    [InlineData("application/json", "{}")]
    [InlineData("application/json", "[]")]
    public async Task UnavailableResponseStillAlertsWhenHealthContractIsMissing(string mediaType, string body)
    {
        var result = await Client(new Handler(_ => new(HttpStatusCode.ServiceUnavailable)
        { Content = new StringContent(body, Encoding.UTF8, mediaType) })).CheckAsync(Target(), 2);
        Assert.Equal("DOWN", result.Status);
        Assert.Equal("HTTP_UNAVAILABLE", result.ReasonCode);
        Assert.Equal(503, result.HttpStatusCode);
        Assert.Empty(result.Components);
    }

    [Theory]
    [InlineData(301, "REDIRECT_BLOCKED")]
    [InlineData(302, "REDIRECT_BLOCKED")]
    [InlineData(401, "AUTH_REQUIRED")]
    [InlineData(403, "AUTH_REQUIRED")]
    [InlineData(404, "ENDPOINT_NOT_FOUND")]
    public async Task UnsupportedEndpointIsNotAReadinessPass(int code, string reason)
    {
        var result = await Client(new Handler(_ => new((HttpStatusCode)code))).CheckAsync(Target(), 2);
        Assert.Equal("UNVERIFIED", result.Status);
        Assert.Equal(reason, result.ReasonCode);
    }

    [Fact]
    public async Task DisabledTargetSendsNoRequestAndHasNoMeasuredTimestamp()
    {
        var handler = new Handler(_ => throw new InvalidOperationException("must not be called"));
        var target = Target(); target.Enabled = false; target.Url = null;
        var result = await Client(handler).CheckAsync(target, 2);
        Assert.Equal("UNCONFIGURED", result.Status);
        Assert.Null(result.CheckedAt);
        Assert.Empty(handler.Methods);
    }

    [Fact]
    public async Task BodyLimitCannotBeBypassedByChunkedContent()
    {
        var handler = new Handler(_ => new(HttpStatusCode.OK)
        { Content = new ChunkContent(new string('x', 65537)) });
        var result = await Client(handler).CheckAsync(Target(), 2);
        Assert.Equal("UNVERIFIED", result.Status);
        Assert.Equal("RESPONSE_TOO_LARGE", result.ReasonCode);
    }

    [Fact]
    public async Task TimeoutIsBoundedAndCancellationPropagatesOnShutdown()
    {
        var client = Client(new SlowHandler());
        var result = await client.CheckAsync(Target(), 1);
        Assert.Equal("TIMEOUT", result.ReasonCode);
        using var stop = new CancellationTokenSource(); stop.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => client.CheckAsync(Target(), 2, stop.Token));
    }

    [Theory]
    [InlineData("http://user:secret@localhost/healthz")]
    [InlineData("https://msgctf.kr/healthz?token=secret")]
    [InlineData("file:///etc/passwd")]
    [InlineData("http://localhost/healthz#part")]
    public void CredentialsAndNonHttpTargetsAreRejected(string url)
    {
        var target = Target(); target.Url = url;
        Assert.False(target.IsValid());
    }

    [Fact]
    public void ProfileBindsSnakeCaseAndRejectsDuplicateTargetIds()
    {
        var config = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string,string?>
        {
            ["platform_monitor:poll_interval_seconds"]="30",
            ["platform_monitor:targets:0:target_id"]="test-backend",
            ["platform_monitor:targets:0:name"]="백엔드",
            ["platform_monitor:targets:0:role"]="BACKEND",
            ["platform_monitor:targets:0:track"]="INTERNAL",
            ["platform_monitor:targets:0:url"]="http://localhost/healthz",
        }).Build();
        var options = config.GetSection(PlatformOptions.SectionName).Get<PlatformOptions>()!;
        Assert.Equal(30, options.PollIntervalSeconds);
        Assert.True(options.IsValid());
        options.Targets.Add(options.Targets[0]);
        Assert.False(options.IsValid());
    }

    [Fact]
    public async Task IncidentThresholdDeduplicationRecoveryAndRestartAreEnforced()
    {
        var file = Path.Combine(Path.GetTempPath(), "sentinel-test-" + Guid.NewGuid() + ".jsonl");
        try
        {
            var failed = true;
            var handler = new Handler(_ => failed ? Response(503,"down","down","ok") : Response(200,"ok","ok","ok"));
            var options = Options.Create(new PlatformOptions
            { Targets = [Target()], FailureThreshold = 3, RecoveryThreshold = 2, EventFile = file });
            var monitor = new PlatformMonitor(options,Client(handler),NullLogger<PlatformMonitor>.Instance);
            await monitor.RefreshAsync(); await monitor.RefreshAsync();
            Assert.Empty(monitor.Events());
            await monitor.RefreshAsync(); await monitor.RefreshAsync();
            Assert.Single(monitor.Events());
            var restored = new PlatformMonitor(options,Client(handler),NullLogger<PlatformMonitor>.Instance);
            await restored.RefreshAsync(); await restored.RefreshAsync(); await restored.RefreshAsync();
            Assert.Single(restored.Events());
            failed = false;
            await restored.RefreshAsync();
            Assert.Single(restored.Events());
            await restored.RefreshAsync();
            Assert.Equal(2,restored.Events().Count);
            Assert.Equal("HEALTHY",restored.Events()[0].Status);
            Assert.DoesNotContain("secret",File.ReadAllText(file));
        }
        finally { if (File.Exists(file)) File.Delete(file); }
    }

    [Fact]
    public async Task StaleResultsAreNotHealthyAndPublicFieldsUseSnakeCase()
    {
        var clock = new Clock();
        var file = Path.Combine(Path.GetTempPath(), "sentinel-test-" + Guid.NewGuid() + ".jsonl");
        var options = Options.Create(new PlatformOptions { Targets=[Target()],EventFile=file });
        var monitor = new PlatformMonitor(options,Client(new Handler(_ => Response(200,"ok","ok","ok"))),
            NullLogger<PlatformMonitor>.Instance,clock);
        await monitor.RefreshAsync();
        Assert.Equal("HEALTHY",monitor.Snapshot().Targets[0].Status);
        clock.Offset = TimeSpan.FromMinutes(2);
        Assert.Equal("STALE",monitor.Snapshot().Targets[0].Status);
        Assert.Contains("sentinel_target_health{target_id=\"test-backend\"} -1",monitor.Metrics());
        var json = JsonSerializer.Serialize(monitor.Snapshot(),PlatformJson.Options);
        Assert.Contains("\"updated_at\"",json);
        Assert.Contains("\"target_id\"",json);
        Assert.DoesNotContain("TargetId",json);
        using var document = JsonDocument.Parse(json);
        Assert.EndsWith("Z",document.RootElement.GetProperty("started_at").GetString());
    }

    [Fact]
    public void CorruptEventHistoryDoesNotStopMonitoringOrInventValidEvents()
    {
        var file = Path.Combine(Path.GetTempPath(), "sentinel-test-" + Guid.NewGuid() + ".jsonl");
        try
        {
            var valid = new MonitorEvent(Guid.NewGuid().ToString(), "test-backend", "INTERNAL",
                "UNKNOWN", "DOWN", "DATABASE_DOWN", DateTimeOffset.UtcNow);
            var serialized = JsonSerializer.Serialize(valid, PlatformJson.Options);
            File.WriteAllLines(file, [serialized, "{broken", serialized.Replace(valid.TargetId, ""),
                serialized.Replace(valid.CreatedAt.UtcDateTime.ToString("O"), "not-a-timestamp"),
                serialized.Replace("\"status\":\"DOWN\"", "\"status\":null")]);
            var options = Options.Create(new PlatformOptions { Targets = [Target()], EventFile = file });
            var monitor = new PlatformMonitor(options,Client(new Handler(_ => Response(200,"ok","ok","ok"))),
                NullLogger<PlatformMonitor>.Instance);
            Assert.Single(monitor.Events());
            Assert.False(monitor.Snapshot().EventStorageHealthy);
            Assert.Equal("PENDING",monitor.Snapshot().Targets[0].Status);
        }
        finally { if (File.Exists(file)) File.Delete(file); }
    }

    private static PlatformTarget Target() => new()
    { TargetId="test-backend",Name="백엔드",Role="BACKEND",Track="INTERNAL",Url="http://localhost/healthz" };
    private static PlatformProbeClient Client(HttpMessageHandler handler) => new(new HttpClient(handler)
    { Timeout = Timeout.InfiniteTimeSpan });
    private static HttpResponseMessage Response(int code,string status,string database,string cache)
        => new((HttpStatusCode)code) { Content = JsonContent.Create(new { status,database,cache }) };
    private sealed class Handler(Func<HttpRequestMessage,HttpResponseMessage> make) : HttpMessageHandler
    {
        public List<HttpMethod> Methods { get; } = [];
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        { token.ThrowIfCancellationRequested(); Methods.Add(request.Method); return Task.FromResult(make(request)); }
    }
    private sealed class SlowHandler : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        { await Task.Delay(10000,token); return Response(200,"ok","ok","ok"); }
    }
    private sealed class ChunkContent : HttpContent
    {
        private readonly byte[] _body;
        public ChunkContent(string body)
        { _body=Encoding.UTF8.GetBytes(body);Headers.ContentType=new("application/json"); }
        protected override Task SerializeToStreamAsync(Stream stream,TransportContext? context)
            => stream.WriteAsync(_body).AsTask();
        protected override bool TryComputeLength(out long length) {length=0;return false;}
    }
    private sealed class Clock : TimeProvider
    {
        public TimeSpan Offset { get; set; }
        public override DateTimeOffset GetUtcNow() => DateTimeOffset.UtcNow + Offset;
    }
}
