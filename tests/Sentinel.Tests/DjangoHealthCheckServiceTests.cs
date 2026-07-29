using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Broker;
using Sentinel.Contracts.Events;
using Sentinel.Contracts.Health;
using Sentinel.Contracts.Instances;
using Sentinel.Contracts.Scheduler;
using Sentinel.Infrastructure.CtfMock;
using Sentinel.Monitor.Django;
using Xunit;

namespace Sentinel.Tests;

public sealed class DjangoHealthCheckServiceTests
{
    [Fact]
    public void AddDjangoHealthMonitor_ShouldRegisterServiceAndHostedWorker()
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["DjangoHealthMonitor:PollInterval"] = "00:00:10",
            })
            .Build();
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddSingleton<ICtfMockClient>(new FakeCtfMockClient());

        services.AddDjangoHealthMonitor(configuration);
        using var provider = services.BuildServiceProvider();

        Assert.IsType<DjangoHealthCheckService>(
            provider.GetRequiredService<IDjangoHealthCheckService>());
        Assert.Contains(
            provider.GetServices<IHostedService>(),
            hostedService => hostedService is DjangoHealthCheckWorker);
    }

    [Fact]
    public async Task CheckAsync_ShouldReturnHealthyResultWithoutAlerts()
    {
        var checkedAt = DateTimeOffset.Parse("2026-07-29T01:00:00Z");
        var client = new FakeCtfMockClient
        {
            DjangoHealth = new DjangoHealthEvent(
                HealthStatus.Healthy,
                HealthStatus.Healthy,
                HealthStatus.Healthy,
                "healthy",
                checkedAt),
            ApiStatus = new CtfMockApiStatus(
                "healthy",
                null,
                new[]
                {
                    new CtfMockApiStatusItem("scoreboard", "/api/scoreboard", "healthy", 120),
                },
                checkedAt),
        };
        var metrics = new FakeDjangoHealthMetrics();
        var service = new DjangoHealthCheckService(client, metrics);

        var result = await service.CheckAsync();

        Assert.True(result.IsHealthy);
        Assert.Equal(HealthStatus.Healthy, result.HealthEvent.Status);
        Assert.Empty(result.AdminAlertCandidates);
        Assert.Equal(1, client.HealthCallCount);
        Assert.Equal(1, client.ApiStatusCallCount);
        Assert.Same(result, metrics.LastRecordedResult);
    }

    [Fact]
    public async Task CheckAsync_ShouldCreateCriticalAlertWhenDatabaseIsUnhealthy()
    {
        var checkedAt = DateTimeOffset.Parse("2026-07-29T01:10:00Z");
        var client = new FakeCtfMockClient
        {
            DjangoHealth = new DjangoHealthEvent(
                HealthStatus.Unhealthy,
                HealthStatus.Unhealthy,
                HealthStatus.Healthy,
                "database_unavailable",
                checkedAt),
            ApiStatus = HealthyApiStatus(checkedAt),
        };
        var metrics = new FakeDjangoHealthMetrics();
        var service = new DjangoHealthCheckService(client, metrics);

        var result = await service.CheckAsync();

        var alert = Assert.Single(result.AdminAlertCandidates);
        Assert.False(result.IsHealthy);
        Assert.Equal(EventSeverity.Critical, alert.Severity);
        Assert.Equal("django-server-monitor", alert.Source);
        Assert.Equal("database_unavailable", alert.Metadata["reason"]);
        Assert.Equal("Unhealthy", alert.Metadata["databaseStatus"]);
        Assert.Same(result, metrics.LastRecordedResult);
    }

    [Fact]
    public async Task CheckAsync_ShouldCreateWarningAlertWhenMainApiTimesOut()
    {
        var checkedAt = DateTimeOffset.Parse("2026-07-29T01:20:00Z");
        var client = new FakeCtfMockClient
        {
            DjangoHealth = new DjangoHealthEvent(
                HealthStatus.Healthy,
                HealthStatus.Healthy,
                HealthStatus.Healthy,
                "healthy",
                checkedAt),
            ApiStatus = new CtfMockApiStatus(
                "unhealthy",
                "django_api_timeout",
                new[]
                {
                    new CtfMockApiStatusItem("login", "/api/auth/login", "timeout", 5000),
                    new CtfMockApiStatusItem("scoreboard", "/api/scoreboard", "timeout", 5000),
                },
                checkedAt),
        };
        var metrics = new FakeDjangoHealthMetrics();
        var service = new DjangoHealthCheckService(client, metrics);

        var result = await service.CheckAsync();

        var alert = Assert.Single(result.AdminAlertCandidates);
        Assert.False(result.IsHealthy);
        Assert.Equal(EventSeverity.Warning, alert.Severity);
        Assert.Equal("django_api_timeout", alert.Metadata["reason"]);
        Assert.Equal("login,scoreboard", alert.Metadata["unhealthyApis"]);
        Assert.Same(result, metrics.LastRecordedResult);
    }

    private static CtfMockApiStatus HealthyApiStatus(DateTimeOffset checkedAt)
    {
        return new CtfMockApiStatus(
            "healthy",
            null,
            new[]
            {
                new CtfMockApiStatusItem("scoreboard", "/api/scoreboard", "healthy", 120),
            },
            checkedAt);
    }

    private sealed class FakeCtfMockClient : ICtfMockClient
    {
        public DjangoHealthEvent DjangoHealth { get; init; } = new(
            HealthStatus.Healthy,
            HealthStatus.Healthy,
            HealthStatus.Healthy,
            "healthy",
            DateTimeOffset.UtcNow);

        public CtfMockApiStatus ApiStatus { get; init; } = HealthyApiStatus(DateTimeOffset.UtcNow);

        public int HealthCallCount { get; private set; }

        public int ApiStatusCallCount { get; private set; }

        public Task<DjangoHealthEvent> GetDjangoHealthAsync(
            CancellationToken cancellationToken = default)
        {
            HealthCallCount++;
            return Task.FromResult(DjangoHealth);
        }

        public Task<CtfMockApiStatus> GetApiStatusAsync(
            CancellationToken cancellationToken = default)
        {
            ApiStatusCallCount++;
            return Task.FromResult(ApiStatus);
        }

        public Task<IReadOnlyList<MonitoredInstance>> GetInstancesAsync(
            CancellationToken cancellationToken = default)
        {
            throw new NotSupportedException();
        }

        public Task<HealthSignal> GetInstanceHealthAsync(
            string instanceId,
            CancellationToken cancellationToken = default)
        {
            throw new NotSupportedException();
        }

        public Task<IReadOnlyList<BrokerAccountStatus>> GetBrokerAccountsAsync(
            CancellationToken cancellationToken = default)
        {
            throw new NotSupportedException();
        }

        public Task<CtfMockPostResult> SendSchedulerSignalAsync(
            SchedulerSignal signal,
            CancellationToken cancellationToken = default)
        {
            throw new NotSupportedException();
        }

        public Task<CtfMockPostResult> SendAdminAlertAsync(
            AdminAlert alert,
            CancellationToken cancellationToken = default)
        {
            throw new NotSupportedException();
        }
    }

    private sealed class FakeDjangoHealthMetrics : IDjangoHealthMetrics
    {
        public DjangoHealthCheckResult? LastRecordedResult { get; private set; }

        public void Record(DjangoHealthCheckResult result)
        {
            LastRecordedResult = result;
        }
    }
}
