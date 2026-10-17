using System.Text;
using Prometheus;
using Sentinel.Monitor.Django;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class PlatformMetricsTests
{
    [Fact]
    public async Task RealBackendMetricsKeepTargetsSeparateAndClearOldHealthyComponents()
    {
        var registry = Metrics.NewCustomRegistry();
        var metrics = new PrometheusDjangoHealthMetrics(registry);
        var healthy = new ProbeObservation("public-backend", "Backend", "BACKEND", "PUBLIC", "backend_healthz",
            "https://example.com/healthz", true, "HEALTHY", "BACKEND_READY", DateTimeOffset.UtcNow, 200, 15,
            new Dictionary<string, string> { ["status"] = "ok", ["database"] = "ok", ["cache"] = "ok" });
        metrics.RecordPlatform(healthy);
        metrics.RecordPlatform(healthy with { TargetId = "external-backend", Status = "UNVERIFIED", Components = new Dictionary<string, string>() });
        using var before = new MemoryStream();
        await registry.CollectAndExportAsTextAsync(before);
        var text = Encoding.UTF8.GetString(before.ToArray());
        Assert.Contains("sentinel_django_component_health{target_id=\"public-backend\",component=\"database\"} 1", text);
        Assert.Contains("sentinel_django_component_health{target_id=\"external-backend\",component=\"database\"} -1", text);
        metrics.RecordPlatform(healthy with { Status = "STALE" });
        using var after = new MemoryStream();
        await registry.CollectAndExportAsTextAsync(after);
        Assert.Contains("sentinel_django_component_health{target_id=\"public-backend\",component=\"database\"} -1", Encoding.UTF8.GetString(after.ToArray()));
    }
}
