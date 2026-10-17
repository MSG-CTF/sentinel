using System.Net;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

[Collection("Discord environment")]
public sealed class DiscordAlertsTests : IDisposable
{
    private readonly string _file = Path.Combine(Path.GetTempPath(), "sentinel-alert-" + Guid.NewGuid() + ".json");
    private readonly Clock _clock = new();
    private readonly Handler _handler = new();
    private readonly string? _old = Environment.GetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL");
    public DiscordAlertsTests() => Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/12345678901234567/" + new string('a', 40));
    private DiscordAlerts Create() => new(Options.Create(new DiscordAlertOptions { Enabled = true, StateFile = _file }), new HttpClient(_handler), _clock);
    private void Observe(DiscordAlerts alerts, string level, int tick)
    {
        _clock.Now = DateTimeOffset.Parse("2026-10-14T12:00:00Z").AddSeconds(tick);
        var sample = new ProbeObservation("bot", "Bot", "DISCORD", "CONTROL", "service_heartbeat", null, true,
            level == "UNKNOWN" ? "STALE" : "HEALTHY", "SERVICE_ACTIVE", _clock.Now, null, null,
            new Dictionary<string, string>(), Risk: new(level, []));
        alerts.Observe(new("test", "observe_only", _clock.Now, _clock.Now, 15, 3, true, [sample]));
    }
    [Fact]
    public async Task TransitionsEscalateOnceAndUnknownNeverFalselyRecovers()
    {
        var alerts=Create(); Observe(alerts,"NORMAL",0); await alerts.DeliverAsync(); Assert.Empty(_handler.Bodies);
        Observe(alerts,"WARNING",1); Observe(alerts,"WARNING",1); Observe(alerts,"WARNING",2); await alerts.DeliverAsync();
        Assert.Single(_handler.Bodies); Observe(alerts,"CRITICAL",3); await alerts.DeliverAsync();
        Observe(alerts,"UNKNOWN",4); Observe(alerts,"NORMAL",5); Observe(alerts,"NORMAL",5); await alerts.DeliverAsync();
        Assert.Equal(2,_handler.Bodies.Count);
        Observe(alerts,"UNKNOWN",6); Observe(alerts,"NORMAL",7); await alerts.DeliverAsync(); Assert.Equal(2,_handler.Bodies.Count);
        Observe(alerts,"NORMAL",8); await alerts.DeliverAsync(); Assert.Equal(3,_handler.Bodies.Count);
        Assert.Contains("복구 확인",_handler.Bodies.Last());
    }
    [Fact]
    public async Task RestartKeepsDeliveredLevelsAndOutbox()
    {
        var alerts=Create(); Observe(alerts,"WARNING",1); await alerts.DeliverAsync();
        alerts=Create(); Observe(alerts,"WARNING",2); await alerts.DeliverAsync(); Assert.Single(_handler.Bodies);
        Observe(alerts,"CRITICAL",3); alerts=Create(); await alerts.DeliverAsync(); Assert.Equal(2,_handler.Bodies.Count);
        alerts=Create(); await alerts.DeliverAsync(); Assert.Equal(2,_handler.Bodies.Count);
    }
    [Fact]
    public async Task RateLimitKeepsPendingNotificationUntilRetryDeadline()
    {
        _handler.Status=HttpStatusCode.TooManyRequests; var alerts=Create(); Observe(alerts,"CRITICAL",1);
        await alerts.DeliverAsync(); await alerts.DeliverAsync(); Assert.Single(_handler.Bodies);
        _clock.Now += TimeSpan.FromSeconds(11); _handler.Status=HttpStatusCode.OK; await alerts.DeliverAsync();
        Assert.Equal(2,_handler.Bodies.Count); await alerts.DeliverAsync(); Assert.Equal(2,_handler.Bodies.Count);
    }
    [Fact]
    public async Task TestMessageIsMarkedAndRateLimitedWithoutCreatingAnIncident()
    {
        var alerts=Create(); Assert.True(alerts.QueueTest()); Assert.False(alerts.QueueTest());
        await alerts.DeliverAsync(); Assert.Contains("실제 장애 알림이 아닙니다",_handler.Bodies.Single());
        Observe(alerts,"NORMAL",1); Observe(alerts,"NORMAL",2); await alerts.DeliverAsync(); Assert.Single(_handler.Bodies);
        var document=JsonDocument.Parse(_handler.Bodies.Single()); Assert.Empty(document.RootElement.GetProperty("allowed_mentions").GetProperty("parse").EnumerateArray());
    }
    [Fact]
    public async Task CorruptJournalDoesNotSendOrInventRecovery()
    {
        File.WriteAllText(_file,"invalid"); var alerts=Create(); Observe(alerts,"CRITICAL",1);
        Assert.False(alerts.QueueTest()); await alerts.DeliverAsync(); Assert.Empty(_handler.Bodies);
    }
    [Fact]
    public async Task CandidateCannotSendEvenWithAnEnabledProfile()
    {
        var previous=Environment.GetEnvironmentVariable("SENTINEL_CANDIDATE");
        try {Environment.SetEnvironmentVariable("SENTINEL_CANDIDATE","true"); var alerts=Create(); Assert.False(alerts.Enabled); Assert.False(alerts.QueueTest()); Observe(alerts,"CRITICAL",1); await alerts.DeliverAsync(); Assert.Empty(_handler.Bodies);}
        finally {Environment.SetEnvironmentVariable("SENTINEL_CANDIDATE",previous);}
    }
    [Fact]
    public async Task RestartCannotResolveAnIncidentAboveWarningClearMargin()
    {
        var alerts=Create();Observe(alerts,"CRITICAL",0);await alerts.DeliverAsync();alerts=Create();
        for(var tick=1;tick<=2;tick++)
        {
            _clock.Now=_clock.Now.AddSeconds(1);
            var sample=new ProbeObservation("bot","Bot","DISCORD","CONTROL","service_heartbeat",null,true,"HEALTHY","SERVICE_ACTIVE",_clock.Now,null,null,new Dictionary<string,string>(),Risk:new("NORMAL",[new("memory_used_percent",79,"%","NORMAL",80,90,75,false)]));
            alerts.Observe(new("test","observe_only",_clock.Now,_clock.Now,15,3,true,[sample]));await alerts.DeliverAsync();
        }
        Assert.Single(_handler.Bodies);
    }
    [Theory]
    [InlineData("https://discord.com.evil.test/api/webhooks/12345678901234567/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData("http://discord.com/api/webhooks/12345678901234567/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")]
    [InlineData("https://discord.com/api/webhooks/12345678901234567/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?x=1")]
    public void WebhookDestinationIsStrictlyBound(string value) => Assert.False(DiscordAlertOptions.ValidWebhook(value));
    private sealed class Clock : TimeProvider
    { public DateTimeOffset Now=DateTimeOffset.Parse("2026-10-14T12:00:00Z"); public override DateTimeOffset GetUtcNow()=>Now; }
    private sealed class Handler : HttpMessageHandler
    {
        public List<string> Bodies=[]; public HttpStatusCode Status=HttpStatusCode.OK;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token)
        {
            Assert.EndsWith("?wait=true",request.RequestUri!.ToString());
            // Parse escaped JSON so assertions inspect user-visible message text.
            var body=await request.Content!.ReadAsStringAsync(token);
            using var json=JsonDocument.Parse(body);
            Bodies.Add(JsonSerializer.Serialize(json, new JsonSerializerOptions { Encoder=System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
            return new(Status){Content=new StringContent("{\"retry_after\":10}")};
        }
    }
    public void Dispose() {Environment.SetEnvironmentVariable("SENTINEL_DISCORD_WEBHOOK_URL",_old); if(File.Exists(_file))File.Delete(_file);}
}
[CollectionDefinition("Discord environment",DisableParallelization=true)]
public sealed class DiscordEnvironmentCollection { }
