using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class SchedulerInventoryLatencyTests
{
    [Fact]
    public async Task CompleteLargeInventoryUsesSlowestPageRatherThanSum()
    {
        using var lab = new Lab();
        var result = await lab.Probe();
        Assert.Equal("HEALTHY", result.Status);
        Assert.Equal(25, lab.Requests);
        Assert.Equal("4829", result.Components["inventory_total"]);
        Assert.Equal("25", result.Components["page_count"]);
        Assert.Equal("6250", result.Components["scan_duration_ms"]);
        Assert.Equal("max_page_response", result.Components["latency_scope"]);
        Assert.Equal(250d, result.LatencyMs);
        Assert.Equal("NORMAL", new RiskEvaluator(SustainedRiskTests.Policy()).Evaluate(result).Level);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(12)]
    [InlineData(24)]
    public async Task SlowPageAnywhereStillTriggersUnchangedCriticalPolicy(int slowPage)
    {
        using var lab = new Lab(slowPage: slowPage);
        var evaluator = new RiskEvaluator(SustainedRiskTests.Policy());
        var first = await lab.Probe();
        Assert.Equal(3600d, first.LatencyMs);
        Assert.Equal("9600", first.Components["scan_duration_ms"]);
        Assert.Equal("NORMAL", evaluator.Evaluate(first).Level);
        lab.Clock.Advance(21000);
        var second = await lab.Probe();
        Assert.Equal(3600d, second.LatencyMs);
        Assert.Equal("CRITICAL", evaluator.Evaluate(second).Level);
    }

    [Theory]
    [InlineData("http", "HTTP_ERROR")]
    [InlineData("body", "HTTP_ERROR")]
    [InlineData("timeout", "TIMEOUT")]
    public async Task FailedLaterPageKeepsLatencyAndNeverClaimsCompleteInventory(string failure, string reason)
    {
        using var lab = new Lab(failure: failure);
        var result = await lab.Probe();
        Assert.Equal(failure == "body" ? "UNVERIFIED" : "DOWN", result.Status);
        Assert.Equal(reason, result.ReasonCode);
        Assert.Equal(2, lab.Requests);
        Assert.Empty(result.Components);
        Assert.Equal(failure == "http" ? 250d : 3700d, result.LatencyMs);
    }

    [Fact]
    public async Task ExistingCriticalRiskClearsAfterTwoDistinctHealthyCompleteScans()
    {
        using var slow = new Lab(slowPage: 12);
        var evaluator = new RiskEvaluator(SustainedRiskTests.Policy());
        evaluator.Evaluate(await slow.Probe());
        slow.Clock.Advance(21000);
        Assert.Equal("CRITICAL", evaluator.Evaluate(await slow.Probe()).Level);
        using var healthy = new Lab(clock: slow.Clock);
        Assert.Equal("CRITICAL", evaluator.Evaluate(await healthy.Probe()).Level);
        Assert.Equal("NORMAL", evaluator.Evaluate(await healthy.Probe()).Level);
    }

    private sealed class Clock : TimeProvider
    {
        private long milliseconds;
        public override long TimestampFrequency => 1000;
        public override long GetTimestamp() => milliseconds;
        public override DateTimeOffset GetUtcNow() => SustainedRiskTests.Epoch.AddMilliseconds(milliseconds);
        internal void Advance(int value) => milliseconds += value;
    }

    private sealed class Lab : HttpMessageHandler
    {
        private readonly string key = "SENTINEL_TEST_" + Guid.NewGuid().ToString("N");
        private readonly int slowPage;
        private readonly string? failure;
        private readonly HttpClient http;
        internal readonly Clock Clock;
        internal int Requests;
        internal Lab(int slowPage = -1, string? failure = null, Clock? clock = null)
        {
            this.slowPage = slowPage; this.failure = failure; Clock = clock ?? new Clock();
            Environment.SetEnvironmentVariable(key, "private-test-token");
            http = new HttpClient(this);
        }
        internal Task<ProbeObservation> Probe() => new PlatformProbeClient(http, timeProvider: Clock).CheckAsync(
            new PlatformTarget { TargetId = "scheduler-api", Name = "Scheduler API", Role = "CONTROL", Track = "CONTROL",
                Kind = "scheduler_inventory", Enabled = true, Url = "https://scheduler.invalid/api/instances", AuthTokenEnv = key }, 4);

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Assert.Equal("scheduler.invalid", request.RequestUri!.Host);
            Assert.Equal("/api/instances", request.RequestUri.AbsolutePath);
            Assert.Contains("limit=200", request.RequestUri.Query);
            Assert.Equal(HttpMethod.Get, request.Method);
            var page = Requests++ % 25;
            Clock.Advance(200);
            if (page == 1 && failure == "http") return Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
            var count = page == 24 ? 29 : 200;
            var rows = Enumerable.Range(page * 200 + 1, count).Select(id => new
            {
                instance_id = new Guid(id, 0, 0, new byte[8]), status = "CLEANED",
                expires_at = SustainedRiskTests.Epoch.ToString("O"), hard_expires_at = SustainedRiskTests.Epoch.ToString("O"),
            });
            var json = JsonSerializer.Serialize(new { code = "SUCCESS", data = new { items = rows, next_cursor = page == 24 ? null : "page-" + page } });
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new TimedContent(Encoding.UTF8.GetBytes(json), Clock, page == slowPage ? 3400 : 50,
                    page == 1 ? failure : null),
            });
        }
        protected override void Dispose(bool disposing)
        {
            Environment.SetEnvironmentVariable(key, null);
            base.Dispose(disposing);
        }
    }

    private sealed class TimedContent : HttpContent
    {
        private readonly byte[] bytes;
        private readonly Clock clock;
        private readonly int delay;
        private readonly string? failure;
        internal TimedContent(byte[] bytes, Clock clock, int delay, string? failure)
        {
            this.bytes = bytes; this.clock = clock; this.delay = delay; this.failure = failure;
            Headers.ContentType = new MediaTypeHeaderValue("application/json");
        }
        protected override bool TryComputeLength(out long length) { length = bytes.Length; return true; }
        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) => stream.WriteAsync(bytes).AsTask();
        protected override Task<Stream> CreateContentReadStreamAsync() => Task.FromResult<Stream>(new TimedStream(bytes, clock, delay, failure));
        protected override Task<Stream> CreateContentReadStreamAsync(CancellationToken cancellationToken) => CreateContentReadStreamAsync();
    }

    private sealed class TimedStream(byte[] bytes, Clock clock, int delay, string? failure) : MemoryStream(bytes)
    {
        private bool measured;
        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            if (!measured)
            {
                measured = true;
                clock.Advance(failure is "body" or "timeout" ? 3500 : delay);
                if (failure == "body") throw new IOException("test body failure");
                if (failure == "timeout") throw new OperationCanceledException("test page timeout");
            }
            return base.ReadAsync(buffer, cancellationToken);
        }
    }
}
