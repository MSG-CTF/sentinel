using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Events;
using Sentinel.Contracts.Health;
using Sentinel.Infrastructure.CtfMock;
using Sentinel.Monitor.Django;
using Xunit;

namespace Sentinel.IntegrationTests;

public sealed class MetricsEndpointTests
{
    [Fact]
    public async Task MetricsEndpoint_ShouldExposeDjangoHealthMetrics()
    {
        await using var factory = new WebApplicationFactory<Program>()
            .WithWebHostBuilder(builder =>
            {
                builder.ConfigureServices(services =>
                {
                    // endpoint 테스트에서는 background worker가 외부 ctf-mock을 호출하지 않도록 제거합니다.
                    services.RemoveAll<IHostedService>();
                });
            });
        var metrics = factory.Services.GetRequiredService<IDjangoHealthMetrics>();
        metrics.Record(new DjangoHealthCheckResult(
            IsHealthy: false,
            HealthEvent: new DjangoHealthEvent(
                HealthStatus.Unhealthy,
                HealthStatus.Unhealthy,
                HealthStatus.Healthy,
                "database_unavailable",
                DateTimeOffset.Parse("2026-07-29T01:00:00Z")),
            ApiStatus: new CtfMockApiStatus(
                "unhealthy",
                "django_api_timeout",
                new[]
                {
                    new CtfMockApiStatusItem("login", "/api/auth/login", "timeout", 5000),
                },
                DateTimeOffset.Parse("2026-07-29T01:00:00Z")),
            AdminAlertCandidates: new[]
            {
                new AdminAlert(
                    "alert-1",
                    EventSeverity.Critical,
                    "Django health check failed",
                    "database unavailable",
                    "django-server-monitor",
                    DateTimeOffset.Parse("2026-07-29T01:00:00Z"),
                    new Dictionary<string, string>()),
            }));
        var client = factory.CreateClient();

        var response = await client.GetStringAsync("/metrics");

        Assert.Contains("sentinel_django_health_status", response, StringComparison.Ordinal);
        Assert.Contains("component=\"database\"} 0", response, StringComparison.Ordinal);
        Assert.Contains("sentinel_django_api_status", response, StringComparison.Ordinal);
        Assert.Contains("api=\"login\",path=\"/api/auth/login\"} 0", response, StringComparison.Ordinal);
        Assert.Contains("sentinel_django_admin_alert_candidates", response, StringComparison.Ordinal);
    }
}
