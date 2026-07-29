using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Events;
using Sentinel.Contracts.Health;
using Sentinel.Infrastructure.CtfMock;

namespace Sentinel.Monitor.Django;

public sealed class DjangoHealthCheckService : IDjangoHealthCheckService
{
    private readonly ICtfMockClient _ctfMockClient;

    public DjangoHealthCheckService(ICtfMockClient ctfMockClient)
    {
        _ctfMockClient = ctfMockClient;
    }

    public async Task<DjangoHealthCheckResult> CheckAsync(
        CancellationToken cancellationToken = default)
    {
        var healthEvent = await _ctfMockClient.GetDjangoHealthAsync(cancellationToken);
        var apiStatus = await _ctfMockClient.GetApiStatusAsync(cancellationToken);
        var alerts = BuildAdminAlertCandidates(healthEvent, apiStatus);

        return new DjangoHealthCheckResult(
            IsHealthy: alerts.Count == 0,
            HealthEvent: healthEvent,
            ApiStatus: apiStatus,
            AdminAlertCandidates: alerts);
    }

    private static IReadOnlyList<AdminAlert> BuildAdminAlertCandidates(
        DjangoHealthEvent healthEvent,
        CtfMockApiStatus apiStatus)
    {
        var alerts = new List<AdminAlert>();

        if (IsDjangoHealthUnhealthy(healthEvent))
        {
            alerts.Add(CreateDjangoHealthAlert(healthEvent));
        }

        var unhealthyApis = apiStatus.Apis
            .Where(api => !IsHealthyStatus(api.Status))
            .Select(api => api.Name)
            .ToArray();

        if (!IsHealthyStatus(apiStatus.Status) || unhealthyApis.Length > 0)
        {
            alerts.Add(CreateApiStatusAlert(apiStatus, unhealthyApis));
        }

        return alerts;
    }

    private static bool IsDjangoHealthUnhealthy(DjangoHealthEvent healthEvent)
    {
        return healthEvent.Status != HealthStatus.Healthy
            || healthEvent.DatabaseStatus != HealthStatus.Healthy
            || healthEvent.CacheStatus != HealthStatus.Healthy;
    }

    private static AdminAlert CreateDjangoHealthAlert(DjangoHealthEvent healthEvent)
    {
        return new AdminAlert(
            AlertId: $"django-health-{healthEvent.CheckedAt:yyyyMMddHHmmss}",
            Severity: EventSeverity.Critical,
            Title: "Django server health check failed",
            Message: "본 CTF Django 서버 health, database, cache 상태 중 하나 이상이 비정상입니다.",
            Source: MonitorDefaults.DjangoServerMonitorName,
            CreatedAt: healthEvent.CheckedAt,
            Metadata: new Dictionary<string, string>
            {
                ["reason"] = healthEvent.Reason,
                ["status"] = healthEvent.Status.ToString(),
                ["databaseStatus"] = healthEvent.DatabaseStatus.ToString(),
                ["cacheStatus"] = healthEvent.CacheStatus.ToString(),
            });
    }

    private static AdminAlert CreateApiStatusAlert(
        CtfMockApiStatus apiStatus,
        IReadOnlyCollection<string> unhealthyApis)
    {
        return new AdminAlert(
            AlertId: $"django-api-{apiStatus.CheckedAt:yyyyMMddHHmmss}",
            Severity: EventSeverity.Warning,
            Title: "Django API health check failed",
            Message: "본 CTF Django 주요 API 응답 상태가 비정상입니다.",
            Source: MonitorDefaults.DjangoServerMonitorName,
            CreatedAt: apiStatus.CheckedAt,
            Metadata: new Dictionary<string, string>
            {
                ["reason"] = apiStatus.Reason ?? apiStatus.Status,
                ["status"] = apiStatus.Status,
                ["unhealthyApis"] = string.Join(",", unhealthyApis),
            });
    }

    private static bool IsHealthyStatus(string status)
    {
        return string.Equals(status, "healthy", StringComparison.OrdinalIgnoreCase);
    }
}
