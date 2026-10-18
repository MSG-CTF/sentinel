using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

[Collection("Discord environment")]
public sealed class BackendAlertPipelineTests : IDisposable
{
    private readonly string _file = Path.Combine(Path.GetTempPath(), "sentinel-backend-alert-" + Guid.NewGuid() + ".json");
    private readonly string? _oldWebhook = Environment.GetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL");

    public BackendAlertPipelineTests() => Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL",
        "https://discord.com/api/webhooks/12345678901234567/" + new string('a', 40));

    private (PlatformMonitor Monitor, DiscordAlerts Alerts) Create(BackendHandler backend, WebhookHandler webhook)
    {
        var options = new PlatformOptions
        {
            EventFile = _file + ".events", TimeoutSeconds = 1,
            Targets = [new() { TargetId = "public-backend", Name = "대회 백엔드", Role = "BACKEND",
                Track = "INTERNAL", Kind = "backend_healthz", Url = "https://backend.invalid/healthz" }],
        };
        if (backend.Mode == "slow")
            options.RiskPolicy.LatencyMs = new() { Warning = 10, Critical = 10000, ClearMargin = 1 };
        var monitor = new PlatformMonitor(Options.Create(options),
            new PlatformProbeClient(new HttpClient(backend) { Timeout = Timeout.InfiniteTimeSpan }),
            NullLogger<PlatformMonitor>.Instance);
        var alerts = new DiscordAlerts(Options.Create(new DiscordAlertOptions { Enabled = true, StateFile = _file }),
            new HttpClient(webhook));
        return (monitor, alerts);
    }

    [Theory]
    [InlineData("limited", "WARNING", "HTTP_RATE_LIMITED", 429)]
    [InlineData("proxy-unavailable", "CRITICAL", "HTTP_UNAVAILABLE", 503)]
    [InlineData("invalid-json", "CRITICAL", "HTTP_UNAVAILABLE", 503)]
    [InlineData("bad-gateway", "CRITICAL", "HTTP_ERROR", 502)]
    [InlineData("offline", "CRITICAL", "CONNECTION_FAILED", null)]
    [InlineData("timeout", "CRITICAL", "TIMEOUT", null)]
    public async Task BackendFailureProducesADirectWebhookWithoutTheBot(string mode, string level, string reason, int? code)
    {
        var backend = new BackendHandler { Mode = mode };
        var webhook = new WebhookHandler();
        var (monitor, alerts) = Create(backend, webhook);
        await monitor.RefreshAsync();
        var snapshot = monitor.Snapshot();
        Assert.Single(snapshot.Targets);
        var observation = snapshot.Targets.Single();
        Assert.Equal(level, observation.Risk!.Level);
        Assert.Equal(reason, observation.ReasonCode);
        Assert.Empty(observation.Components);
        alerts.Observe(snapshot);
        await alerts.DeliverAsync();
        // Polling the same measurement cannot create duplicate incident messages.
        alerts.Observe(snapshot);
        await alerts.DeliverAsync();
        var message = Assert.Single(webhook.Messages);
        var embed = message.GetProperty("embeds")[0];
        Assert.Equal((level == "WARNING" ? "주의 · " : "위험 · ") + "대회 백엔드", embed.GetProperty("title").GetString());
        var fields = embed.GetProperty("fields").EnumerateArray().ToDictionary(
            x => x.GetProperty("name").GetString()!, x => x.GetProperty("value").GetString()!);
        Assert.Equal("public-backend", fields["대상"]);
        Assert.Equal("NORMAL → " + level, fields["상태"]);
        if (code is not null) Assert.Contains("HTTP " + code, fields["감지 근거"]);
        Assert.DoesNotContain("backend-private-body", message.GetRawText());
        Assert.Empty(message.GetProperty("allowed_mentions").GetProperty("parse").EnumerateArray());
    }

    [Fact]
    public async Task ShortHealthyBackendLatencySpikeDoesNotSendWarningOrRecovery()
    {
        var backend = new BackendHandler { Mode = "slow" };
        var webhook = new WebhookHandler();
        var (monitor, alerts) = Create(backend, webhook);
        await monitor.RefreshAsync();
        Assert.Equal("WARNING", monitor.Snapshot().Targets.Single().Risk!.Level);
        alerts.Observe(monitor.Snapshot()); await alerts.DeliverAsync();
        backend.Mode = "healthy";
        for (var index = 0; index < 4; index++)
        {
            await monitor.RefreshAsync(); alerts.Observe(monitor.Snapshot()); await alerts.DeliverAsync();
        }
        Assert.Empty(webhook.Messages);
    }

    [Fact]
    public async Task LimitedThenUnavailableThenHealthySendsEscalationAndVerifiedRecovery()
    {
        var backend = new BackendHandler { Mode = "limited" };
        var webhook = new WebhookHandler();
        var (monitor, alerts) = Create(backend, webhook);
        async Task Check()
        {
            await monitor.RefreshAsync();
            alerts.Observe(monitor.Snapshot());
            await alerts.DeliverAsync();
        }
        await Check();
        backend.Mode = "proxy-unavailable"; await Check();
        backend.Mode = "healthy"; await Check();
        Assert.Equal(2, webhook.Messages.Count);
        await Check();
        Assert.Equal(3, webhook.Messages.Count);
        Assert.Equal("복구 확인 · 대회 백엔드", webhook.Messages.Last().GetProperty("embeds")[0].GetProperty("title").GetString());
    }

    private sealed class BackendHandler : HttpMessageHandler
    {
        public string Mode = "healthy";
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("backend.invalid", request.RequestUri!.Host);
            if (Mode == "offline") throw new HttpRequestException("backend-private-body");
            if (Mode == "timeout") await Task.Delay(Timeout.Infinite, token);
            if (Mode == "slow") await Task.Delay(30, token);
            return Mode switch
            {
                "limited" => new(HttpStatusCode.TooManyRequests) { Content = new StringContent("backend-private-body") },
                "proxy-unavailable" => new(HttpStatusCode.ServiceUnavailable) { Content = new StringContent("<html>backend-private-body</html>") },
                "invalid-json" => new(HttpStatusCode.ServiceUnavailable) { Content = new StringContent("{broken", System.Text.Encoding.UTF8, "application/json") },
                "bad-gateway" => new(HttpStatusCode.BadGateway),
                _ => new(HttpStatusCode.OK) { Content = JsonContent.Create(new { status = "ok", database = "ok", cache = "ok" }) },
            };
        }
    }

    private sealed class WebhookHandler : HttpMessageHandler
    {
        public List<JsonElement> Messages = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Assert.Equal(HttpMethod.Post, request.Method);
            Assert.Equal("discord.com", request.RequestUri!.Host);
            Assert.Equal("?wait=true", request.RequestUri.Query);
            using var message = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            Messages.Add(message.RootElement.Clone());
            return new(HttpStatusCode.OK) { Content = new StringContent("{}") };
        }
    }

    public void Dispose()
    {
        Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL", _oldWebhook);
        if (File.Exists(_file)) File.Delete(_file);
        if (File.Exists(_file + ".events")) File.Delete(_file + ".events");
    }
}
