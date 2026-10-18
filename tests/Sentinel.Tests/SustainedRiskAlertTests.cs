using System.Net;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

[Collection("Discord environment")]
public sealed class SustainedRiskAlertTests : IDisposable
{
    private readonly string _file = Path.Combine(Path.GetTempPath(), "sentinel-sustained-" + Guid.NewGuid() + ".json");
    private readonly string? _oldWebhook = Environment.GetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL");
    private readonly Clock _clock = new();
    private readonly Handler _handler = new();
    private readonly RiskPolicy _policy = SustainedRiskTests.Policy();
    private RiskEvaluator _risk;
    private readonly DiscordAlerts _alerts;

    public SustainedRiskAlertTests()
    {
        Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL",
            "https://discord.com/api/webhooks/12345678901234567/" + new string('a', 40));
        _risk = new(_policy);
        _alerts = new(Options.Create(new DiscordAlertOptions { Enabled = true, StateFile = _file }), new HttpClient(_handler), _clock);
    }

    private async Task Report(int tick, double cpu = 99, double disk = 60, double latency = 20,
        string status = "HEALTHY", string kind = "service_heartbeat")
    {
        _clock.Now = SustainedRiskTests.Epoch.AddSeconds(tick);
        var sample = SustainedRiskTests.Sample(tick, cpu: cpu, disk: disk, latency: latency, status: status, kind: kind);
        sample = sample with { Risk = _risk.Evaluate(sample) };
        var snapshot = new PlatformSnapshot("test", "observe_only", SustainedRiskTests.Epoch, _clock.Now, 15, 3, true,
            [sample], RiskPolicy: _policy, StaleAfterSeconds: 39);
        _alerts.Observe(snapshot); await _alerts.DeliverAsync();
        _alerts.Observe(snapshot); await _alerts.DeliverAsync();
    }

    [Fact]
    public async Task BriefWorkloadAndEightyFourPercentDiskSendNeitherWarningNorRecovery()
    {
        await Report(0, disk: 84.5); await Report(60, disk: 84.5);
        foreach (var tick in new[] { 120, 180, 240, 300 }) await Report(tick, cpu: 40, disk: 84.5);
        Assert.Empty(_handler.Levels);
    }

    [Fact]
    public async Task SustainedRiskEscalatesOnceAndNeedsFreshRecoveryAfterAGap()
    {
        foreach (var tick in new[] { 0, 60, 120, 180, 240, 300 }) await Report(tick);
        Assert.Equal(new[] { "WARNING", "CRITICAL" }, _handler.Levels);
        await Report(360, cpu: 80);
        await Report(420, cpu: 80, status: "STALE");
        await Report(480, cpu: 80);
        Assert.Equal(2, _handler.Levels.Count);
        await Report(540, cpu: 80); await Report(600, cpu: 80);
        Assert.Equal(new[] { "WARNING", "CRITICAL", "NORMAL" }, _handler.Levels);
    }

    [Fact]
    public async Task CollectorRestartCannotRecoverAnOpenIncidentWhileCpuIsStillHigh()
    {
        foreach (var tick in new[] { 0, 60, 120, 180, 240, 300 }) await Report(tick);
        _risk = new(_policy);
        await Report(360); await Report(420);
        Assert.Equal(new[] { "WARNING", "CRITICAL" }, _handler.Levels);
        await Report(480, cpu: 40); await Report(540, cpu: 40);
        Assert.Equal(new[] { "WARNING", "CRITICAL", "NORMAL" }, _handler.Levels);
    }

    [Fact]
    public async Task ConfirmedLatencySendsAtTheSameTimeAsTheDashboard()
    {
        foreach (var tick in new[] { 0, 15, 30, 45, 60, 75 }) await Report(tick, latency: 2100, kind: "http");
        Assert.Empty(_handler.Levels);
        await Report(90, latency: 2100, kind: "http");
        Assert.Equal("WARNING", Assert.Single(_handler.Levels));
        await Report(105, latency: 20, status: "DOWN", kind: "http");
        Assert.Equal(new[] { "WARNING", "CRITICAL" }, _handler.Levels);
    }

    [Fact]
    public async Task DiskExhaustionBypassesCpuConfirmation()
    { await Report(0, disk: 95); Assert.Equal("CRITICAL", Assert.Single(_handler.Levels)); }

    private sealed class Clock : TimeProvider
    {
        public DateTimeOffset Now = SustainedRiskTests.Epoch;
        public override DateTimeOffset GetUtcNow() => Now;
    }
    private sealed class Handler : HttpMessageHandler
    {
        public List<string> Levels = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            var transition = body.RootElement.GetProperty("embeds")[0].GetProperty("fields")[1].GetProperty("value").GetString()!;
            Levels.Add(transition.Split(' ').Last());
            return new(HttpStatusCode.OK) { Content = new StringContent("{}") };
        }
    }
    public void Dispose()
    {
        Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL", _oldWebhook);
        if (File.Exists(_file)) File.Delete(_file);
    }
}
