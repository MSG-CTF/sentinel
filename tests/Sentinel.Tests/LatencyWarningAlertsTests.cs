using System.Net;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

[Collection("Discord environment")]
public sealed class LatencyWarningAlertsTests : IDisposable
{
    private static readonly DateTimeOffset Epoch = DateTimeOffset.Parse("2026-10-18T08:00:00Z");
    private readonly string _file = Path.Combine(Path.GetTempPath(), "sentinel-latency-" + Guid.NewGuid() + ".json");
    private readonly string? _previousWebhook = Environment.GetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL");
    private readonly Clock _clock = new();
    private readonly Handler _handler = new();

    public LatencyWarningAlertsTests() => Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL",
        "https://discord.com/api/webhooks/12345678901234567/" + new string('a', 40));
    private DiscordAlerts Create() => new(Options.Create(new DiscordAlertOptions { Enabled = true, StateFile = _file }),
        new HttpClient(_handler), _clock);

    private void Observe(DiscordAlerts alerts, int tick, double latency = 1100, string? level = null,
        string status = "HEALTHY", int? measuredAt = null, bool enabled = true, bool recovering = false,
        IReadOnlyList<RiskSignal>? signals = null, int staleAfterSeconds = 41, string target = "scheduler-api")
    {
        _clock.Now = Epoch.AddSeconds(tick);
        level ??= latency >= 3000 ? "CRITICAL" : latency >= 1000 ? "WARNING" : "NORMAL";
        var observation = new ProbeObservation(target, "스케줄러 인스턴스 API", "SCHEDULER", "CONTROL",
            "scheduler_inventory", null, enabled, status,
            status == "DEGRADED" ? "HTTP_RATE_LIMITED" : status == "DOWN" ? "HTTP_UNAVAILABLE" : "SCHEDULER_QUERY_READY",
            Epoch.AddSeconds(measuredAt ?? tick), status == "DEGRADED" ? 429 : status == "DOWN" ? 503 : 200, latency,
            new Dictionary<string, string>(), Risk: new(level, signals ??
                [new("latency_ms", latency, "ms", level, 1000, 3000, level == "CRITICAL" ? 2800 : 800, recovering)]));
        alerts.Observe(new("test", "observe_only", Epoch, _clock.Now, 15, 3, true, [observation], StaleAfterSeconds: staleAfterSeconds));
    }

    [Theory]
    [InlineData(1054.3)]
    [InlineData(1057.7)]
    [InlineData(1113.6)]
    [InlineData(1116.4)]
    [InlineData(1117.2)]
    public async Task IsolatedProductionLatencySpikeSendsNeitherWarningNorRecovery(double spike)
    {
        var alerts = Create();
        Observe(alerts, 0, spike); await alerts.DeliverAsync();
        Observe(alerts, 15, 360, "WARNING", recovering: true); await alerts.DeliverAsync();
        Observe(alerts, 30, 360); Observe(alerts, 45, 360); await alerts.DeliverAsync();
        Assert.Empty(_handler.Messages);
    }

    [Fact]
    public async Task SustainedLatencyWarnsOnceThenRecoversAfterTwoNormalAssessments()
    {
        var alerts = Create();
        foreach (var tick in new[] { 0, 15, 30, 45 })
        { Observe(alerts, tick); await alerts.DeliverAsync(); Assert.Empty(_handler.Messages); }
        Observe(alerts, 60); await alerts.DeliverAsync();
        Assert.Equal("WARNING", Assert.Single(_handler.Messages));
        Observe(alerts, 75); await alerts.DeliverAsync(); Assert.Single(_handler.Messages);
        Observe(alerts, 90, 360); await alerts.DeliverAsync(); Assert.Single(_handler.Messages);
        Observe(alerts, 105, 360); await alerts.DeliverAsync();
        Assert.Equal(new[] { "WARNING", "NORMAL" }, _handler.Messages);
    }

    [Fact]
    public async Task RepeatedOrOutOfOrderMeasurementsCannotConfirmLatency()
    {
        var alerts = Create(); Observe(alerts, 0);
        foreach (var tick in new[] { 15, 30, 40, 60, 75 })
        { Observe(alerts, tick, measuredAt: 0); await alerts.DeliverAsync(); }
        Observe(alerts, 80, measuredAt: -1); await alerts.DeliverAsync();
        Assert.Empty(_handler.Messages);
    }

    [Fact]
    public async Task SparseMeasurementsDoNotTurnElapsedTimeIntoConfirmedLatency()
    {
        var alerts = Create();
        foreach (var tick in new[] { 0, 45, 90, 135, 180 })
        { Observe(alerts, tick); await alerts.DeliverAsync(); }
        Assert.Empty(_handler.Messages);
    }

    [Fact]
    public async Task ALongerPollingIntervalStillRequiresThreeDistinctMeasurements()
    {
        var alerts = Create();
        Observe(alerts, 0, staleAfterSeconds: 150); Observe(alerts, 60, staleAfterSeconds: 150);
        await alerts.DeliverAsync(); Assert.Empty(_handler.Messages);
        Observe(alerts, 90, staleAfterSeconds: 150); await alerts.DeliverAsync();
        Assert.Equal("WARNING", Assert.Single(_handler.Messages));
    }

    [Fact]
    public async Task RepeatingShortSpikesDoNotCreateWarningRecoveryPairsOrMuteLaterDanger()
    {
        var alerts = Create();
        for (var cycle = 0; cycle < 6; cycle++)
        {
            var tick = cycle * 1350;
            Observe(alerts, tick, 1116.4);
            Observe(alerts, tick + 15, 360, "WARNING", recovering: true);
            Observe(alerts, tick + 30, 360); Observe(alerts, tick + 45, 360);
            await alerts.DeliverAsync(); Assert.Empty(_handler.Messages);
            if (cycle % 2 == 0) alerts = Create();
        }
        Observe(alerts, 5 * 1350 + 60, 3100); await alerts.DeliverAsync();
        Assert.Equal("CRITICAL", Assert.Single(_handler.Messages));
    }

    [Fact]
    public async Task MeasurementsFromDifferentTargetsDoNotShareAConfirmationStreak()
    {
        var alerts = Create(); Observe(alerts, 0); Observe(alerts, 15, target: "another-api");
        Observe(alerts, 30); Observe(alerts, 45, target: "another-api"); Observe(alerts, 60, target: "another-api");
        await alerts.DeliverAsync(); Assert.Empty(_handler.Messages);
        Observe(alerts, 75, target: "another-api"); await alerts.DeliverAsync();
        Assert.Equal("WARNING", Assert.Single(_handler.Messages));
    }

    [Theory]
    [InlineData("STALE", true, 31)]
    [InlineData("UNVERIFIED", true, 31)]
    [InlineData("HEALTHY", false, 31)]
    [InlineData("HEALTHY", true, 100)]
    [InlineData("HEALTHY", true, -20)]
    public async Task MissingDisabledExpiredOrFutureEvidenceBreaksThePendingStreak(string status, bool enabled, int measuredAt)
    {
        var alerts = Create(); Observe(alerts, 0); Observe(alerts, 15); Observe(alerts, 30);
        Observe(alerts, 31, status: status, enabled: enabled, measuredAt: measuredAt,
            level: status is "STALE" or "UNVERIFIED" ? "UNKNOWN" : "WARNING");
        Observe(alerts, 45); Observe(alerts, 60); await alerts.DeliverAsync();
        Assert.Empty(_handler.Messages);
    }

    [Fact]
    public async Task CollectorRecoveryHysteresisDoesNotConfirmANewLatencyWarning()
    {
        var alerts = Create(); Observe(alerts, 0);
        foreach (var tick in new[] { 15, 30, 45, 60, 75, 90 })
        { Observe(alerts, tick, 900, "WARNING", recovering: true); await alerts.DeliverAsync(); }
        Assert.Empty(_handler.Messages);
    }

    [Fact]
    public async Task RestartRequiresFreshLatencyEvidenceInsteadOfPromotingAnOldCandidate()
    {
        var alerts = Create(); Observe(alerts, 0); Observe(alerts, 15); Observe(alerts, 30);
        alerts = Create();
        foreach (var tick in new[] { 45, 60, 75, 90 })
        { Observe(alerts, tick); await alerts.DeliverAsync(); Assert.Empty(_handler.Messages); }
        Observe(alerts, 105); await alerts.DeliverAsync();
        Assert.Equal("WARNING", Assert.Single(_handler.Messages));
    }

    [Theory]
    [InlineData("HEALTHY", 3000, "CRITICAL")]
    [InlineData("DOWN", 100, "CRITICAL")]
    [InlineData("DEGRADED", 1100, "WARNING")]
    public async Task CriticalAndErrorResponsesBypassTheLatencyDelay(string status, double latency, string level)
    {
        var alerts = Create(); Observe(alerts, 0); await alerts.DeliverAsync();
        Observe(alerts, 15, latency, level, status); await alerts.DeliverAsync();
        Assert.Equal(level, Assert.Single(_handler.Messages));
    }

    [Fact]
    public async Task ResourceWarningIsImmediateEvenWhenLatencyIsAlsoAboveWarning()
    {
        var alerts = Create();
        Observe(alerts, 0, signals:
            [new("latency_ms", 1100, "ms", "WARNING", 1000, 3000, 800, false),
             new("cpu_used_percent", 85, "%", "WARNING", 80, 95, 75, false)]);
        await alerts.DeliverAsync();
        Assert.Equal("WARNING", Assert.Single(_handler.Messages));
    }

    [Fact]
    public async Task ReliefOfAnAlreadyNotifiedCriticalIncidentIsNotDelayed()
    {
        var alerts = Create(); Observe(alerts, 0, 3100); await alerts.DeliverAsync();
        Observe(alerts, 15); await alerts.DeliverAsync();
        Assert.Equal(new[] { "CRITICAL", "WARNING" }, _handler.Messages);
    }

    private sealed class Clock : TimeProvider
    { public DateTimeOffset Now = Epoch; public override DateTimeOffset GetUtcNow() => Now; }
    private sealed class Handler : HttpMessageHandler
    {
        public List<string> Messages = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            var transition = body.RootElement.GetProperty("embeds")[0].GetProperty("fields")[1].GetProperty("value").GetString()!;
            Messages.Add(transition.Split(' ').Last());
            return new(HttpStatusCode.OK) { Content = new StringContent("{}") };
        }
    }
    public void Dispose()
    {
        Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL", _previousWebhook);
        if (File.Exists(_file)) File.Delete(_file);
    }
}
