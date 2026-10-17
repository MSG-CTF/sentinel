using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.WebUtilities;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Instances;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class SchedulerPaginationTests
{
    private static JsonObject Row(int index, string status = "RUNNING") => new()
    {
        ["instance_id"] = $"80000000-0000-4000-8000-{index:x12}", ["status"] = status,
        ["expires_at"] = "2099-01-01T00:00:00Z", ["hard_expires_at"] = "2099-01-02T00:00:00Z",
        ["service_url"] = "http://untrusted.invalid/secret", ["runtime_type"] = "VM",
    };
    private static JsonObject Page(IEnumerable<JsonObject> rows, string? next = null) => new()
    {
        ["code"] = "SUCCESS", ["data"] = new JsonObject
        {
            ["items"] = new JsonArray(rows.Select(row => (JsonNode)row).ToArray()), ["next_cursor"] = next,
        },
    };
    private static HttpResponseMessage Json(JsonNode page, HttpStatusCode status = HttpStatusCode.OK) => new(status)
    { Content = new StringContent(page.ToJsonString(), Encoding.UTF8, "application/json") };

    [Theory]
    [InlineData(0)]
    [InlineData(21)]
    [InlineData(200)]
    [InlineData(201)]
    [InlineData(437)]
    public async Task BothConsumersReadEveryPageAndPreserveOlderCleanupRows(int count)
    {
        var expected = Enumerable.Range(1, count).Select(i => Row(i, i == count ? "CLEANUP_PENDING" : "RUNNING")).ToArray();
        using var lab = new Lab(request =>
        {
            var query = QueryHelpers.ParseQuery(request.RequestUri!.Query);
            Assert.Equal("200", query["limit"]);
            Assert.DoesNotContain("status", query.Keys);
            Assert.DoesNotContain("created_after", query.Keys);
            var offset = query.TryGetValue("cursor", out var cursor) ? int.Parse(cursor.ToString()) : 0;
            return Json(Page(expected.Skip(offset).Take(200).Select(row => (JsonObject)row.DeepClone()),
                offset + 200 < count ? (offset + 200).ToString() : null));
        });
        await lab.Inventory.RefreshAsync();
        var snapshot = lab.Inventory.Snapshot();
        Assert.True(snapshot.CompleteInventory);
        Assert.Equal("COMPLETE", snapshot.Status);
        Assert.Equal(expected.Select(row => row["instance_id"]!.GetValue<string>()), snapshot.Instances.Select(row => row.InstanceId));
        if (count > 0) Assert.Equal("CLEANUP_PENDING", snapshot.Instances[^1].SchedulerStatus);
        var probe = await lab.Probe();
        Assert.Equal("HEALTHY", probe.Status);
        Assert.Equal(count.ToString(), probe.Components["inventory_total"]);
        Assert.Equal(Math.Max(1, (count + 199) / 200).ToString(), probe.Components["page_count"]);
        Assert.Equal(2 * Math.Max(1, (count + 199) / 200), lab.Requests.Count);
        var output = JsonSerializer.Serialize(snapshot, PlatformJson.Options) + JsonSerializer.Serialize(probe, PlatformJson.Options);
        Assert.DoesNotContain("private-token", output);
        Assert.DoesNotContain("untrusted.invalid", output);
    }

    [Fact]
    public async Task OpaqueCursorIsEncodedAndLegacyProfileUsesNewEndpoint()
    {
        const string cursor = "https://untrusted.invalid/?a=1&status=CLEANED+#fragment";
        using var lab = new Lab(request =>
        {
            var query = QueryHelpers.ParseQuery(request.RequestUri!.Query);
            Assert.Equal("scheduler.local", request.RequestUri.Host);
            Assert.Equal("/api/instances", request.RequestUri.AbsolutePath);
            if (!query.ContainsKey("cursor")) return Json(Page([Row(1)], cursor));
            Assert.Equal(cursor, query["cursor"]);
            Assert.Equal(2, query.Count);
            return Json(Page([Row(2)]));
        }, legacy: true);
        await lab.Inventory.RefreshAsync();
        Assert.Equal(2, lab.Inventory.Snapshot().Instances.Count);
        Assert.Equal("HEALTHY", (await lab.Probe()).Status);
        Assert.Equal(4, lab.Requests.Count);
    }

    [Theory]
    [InlineData("duplicate")]
    [InlineData("cursor_cycle")]
    [InlineData("missing_cursor")]
    [InlineData("camel_cursor")]
    [InlineData("empty_cursor")]
    [InlineData("numeric_cursor")]
    [InlineData("long_cursor")]
    [InlineData("empty_nonfinal")]
    [InlineData("legacy_array")]
    [InlineData("invalid_row")]
    [InlineData("over_limit")]
    public async Task BadLaterPageNeverPublishesASuccessfulPrefix(string fault)
    {
        using var lab = new Lab(request =>
        {
            if (!QueryHelpers.ParseQuery(request.RequestUri!.Query).ContainsKey("cursor"))
                return Json(Page([Row(1)], "page-two"));
            var page = Page([Row(fault == "duplicate" ? 1 : 2)]);
            var data = (JsonObject)page["data"]!;
            switch (fault)
            {
                case "cursor_cycle": data["next_cursor"] = "page-two"; break;
                case "missing_cursor": data.Remove("next_cursor"); break;
                case "camel_cursor": data.Remove("next_cursor"); data["nextCursor"] = null; break;
                case "empty_cursor": data["next_cursor"] = ""; break;
                case "numeric_cursor": data["next_cursor"] = 2; break;
                case "long_cursor": data["next_cursor"] = new string('a', 1025); break;
                case "empty_nonfinal": data["items"] = new JsonArray(); data["next_cursor"] = "page-three"; break;
                case "legacy_array": page["data"] = new JsonArray(Row(2)); break;
                case "invalid_row": data["items"]![0]!["status"] = "UNKNOWN"; break;
                case "over_limit": return Json(Page(Enumerable.Range(2, 201).Select(i => Row(i))));
            }
            return Json(page);
        });
        await lab.Inventory.RefreshAsync();
        AssertFailure(lab.Inventory.Snapshot());
        var probe = await lab.Probe();
        Assert.Equal("UNVERIFIED", probe.Status);
        Assert.Equal("INVALID_INVENTORY_CONTRACT", probe.ReasonCode);
        Assert.Empty(probe.Components);
        Assert.Equal(4, lab.Requests.Count);
    }

    [Theory]
    [InlineData(401, "UNVERIFIED", "AUTH_REQUIRED")]
    [InlineData(302, "UNVERIFIED", "REDIRECT_BLOCKED")]
    [InlineData(404, "UNVERIFIED", "ENDPOINT_NOT_FOUND")]
    [InlineData(429, "DEGRADED", "HTTP_RATE_LIMITED")]
    [InlineData(503, "DOWN", "HTTP_ERROR")]
    public async Task LaterHttpFailureClearsEarlierSuccessAndNextScanStartsAtFirstPage(int code, string status, string reason)
    {
        var fail = false;
        using var lab = new Lab(request =>
        {
            if (!QueryHelpers.ParseQuery(request.RequestUri!.Query).ContainsKey("cursor")) return Json(Page([Row(1)], "two"));
            return Json(Page([Row(2)]), fail ? (HttpStatusCode)code : HttpStatusCode.OK);
        });
        await lab.Inventory.RefreshAsync();
        Assert.True(lab.Inventory.Snapshot().CompleteInventory);
        fail = true;
        await lab.Inventory.RefreshAsync();
        AssertFailure(lab.Inventory.Snapshot());
        var probe = await lab.Probe();
        Assert.Equal(status, probe.Status);
        Assert.Equal(reason, probe.ReasonCode);
        Assert.Equal(code, probe.HttpStatusCode);
        Assert.Empty(probe.Components);
        fail = false;
        await lab.Inventory.RefreshAsync();
        Assert.True(lab.Inventory.Snapshot().CompleteInventory);
        Assert.Equal(2, lab.Inventory.Snapshot().Instances.Count);
        Assert.Equal(8, lab.Requests.Count);
    }

    [Fact]
    public async Task EmptyTerminalPageIsAllowedAfterNonemptyPage()
    {
        using var lab = new Lab(request => Json(QueryHelpers.ParseQuery(request.RequestUri!.Query).ContainsKey("cursor")
            ? Page([]) : Page([Row(1)], "two")));
        await lab.Inventory.RefreshAsync();
        Assert.True(lab.Inventory.Snapshot().CompleteInventory);
        Assert.Single(lab.Inventory.Snapshot().Instances);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task TimeoutOrCallerCancellationDuringLaterPageCannotComplete(bool cancelCaller)
    {
        using var lab = new Lab(async (request, token) =>
        {
            if (!QueryHelpers.ParseQuery(request.RequestUri!.Query).ContainsKey("cursor")) return Json(Page([Row(1)], "two"));
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
            throw new InvalidOperationException("Cancellation was not applied");
        });
        using var stop = new CancellationTokenSource();
        if (cancelCaller) stop.CancelAfter(TimeSpan.FromMilliseconds(100));
        if (cancelCaller) await Assert.ThrowsAnyAsync<OperationCanceledException>(() => lab.Inventory.RefreshAsync(stop.Token));
        else
        {
            await lab.Inventory.RefreshAsync().WaitAsync(TimeSpan.FromSeconds(5));
            AssertFailure(lab.Inventory.Snapshot());
            Assert.Equal("UPSTREAM_TIMEOUT", lab.Inventory.Snapshot().ReasonCode);
            var probe = await lab.Probe();
            Assert.Equal("DOWN", probe.Status);
            Assert.Equal("TIMEOUT", probe.ReasonCode);
        }
        Assert.False(lab.Inventory.Snapshot().CompleteInventory);
        Assert.Empty(lab.Inventory.Snapshot().Instances);
    }

    private static void AssertFailure(InstanceInventorySnapshot snapshot)
    {
        Assert.Equal("UNAVAILABLE", snapshot.Status);
        Assert.False(snapshot.CompleteInventory);
        Assert.Empty(snapshot.Instances);
    }

    [Theory]
    [InlineData("/api/instances?status=RUNNING")]
    [InlineData("/api/instances?cursor=skip-older")]
    [InlineData("/api/instances#fragment")]
    [InlineData("/other-list")]
    public void FilteredOrUnrelatedConfigurationCannotClaimFullCoverage(string path)
    {
        var url = "http://scheduler.local" + path;
        Assert.False(new InstanceInventoryOptions { Enabled = true, SchedulerUrl = url }.IsValid());
        Assert.False(new PlatformTarget { TargetId = "scheduler", Name = "Scheduler", Role = "CONTROL", Track = "CONTROL",
            Kind = "scheduler_inventory", Url = url, AuthTokenEnv = "SCHEDULER_API_TOKEN" }.IsValid());
    }

    [Theory]
    [InlineData("json", "INVALID_JSON")]
    [InlineData("html", "EXPECTED_JSON")]
    [InlineData("oversize", "RESPONSE_TOO_LARGE")]
    public async Task InvalidLaterPayloadCannotPublishFirstPage(string fault, string reason)
    {
        using var lab = new Lab(request =>
        {
            if (!QueryHelpers.ParseQuery(request.RequestUri!.Query).ContainsKey("cursor")) return Json(Page([Row(1)], "two"));
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(
                fault == "oversize" ? new string('x', 2097153) : "{", Encoding.UTF8, fault == "html" ? "text/html" : "application/json") };
        });
        await lab.Inventory.RefreshAsync();
        AssertFailure(lab.Inventory.Snapshot());
        Assert.Equal(reason, lab.Inventory.Snapshot().ReasonCode);
        Assert.NotEqual("HEALTHY", (await lab.Probe()).Status);
    }

    private sealed class Lab : IDisposable
    {
        private readonly string _key = "PAGINATION_TEST_" + Guid.NewGuid().ToString("N").ToUpperInvariant();
        private readonly string _url;
        private readonly HttpClient _http;
        public InstanceInventory Inventory { get; }
        public List<Uri> Requests { get; } = [];
        public Lab(Func<HttpRequestMessage, HttpResponseMessage> handler, bool legacy = false)
            : this((request, _) => Task.FromResult(handler(request)), legacy) { }
        public Lab(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> handler, bool legacy = false)
        {
            _url = "http://scheduler.local/api/instances" + (legacy ? "/recent" : "");
            Environment.SetEnvironmentVariable(_key, "private-token");
            _http = new HttpClient(new Handler((request, token) =>
            {
                Requests.Add(request.RequestUri!);
                Assert.Equal("scheduler.local", request.RequestUri!.Host);
                Assert.Equal(HttpMethod.Get, request.Method);
                Assert.Equal("Bearer", request.Headers.Authorization?.Scheme);
                Assert.Equal("private-token", request.Headers.Authorization?.Parameter);
                return handler(request, token);
            }));
            Inventory = new(_http, Options.Create(new InstanceInventoryOptions
                { Enabled = true, SchedulerUrl = _url, SchedulerTokenEnv = _key, TimeoutSeconds = 1 }));
        }
        public Task<ProbeObservation> Probe() => new PlatformProbeClient(_http).CheckAsync(new PlatformTarget
        {
            TargetId = "scheduler", Name = "Scheduler", Role = "SCHEDULER", Track = "CONTROL",
            Kind = "scheduler_inventory", Url = _url, AuthTokenEnv = _key,
        }, 1);
        public void Dispose() { _http.Dispose(); Environment.SetEnvironmentVariable(_key, null); }
    }
    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token) => handler(request, token);
    }
}
