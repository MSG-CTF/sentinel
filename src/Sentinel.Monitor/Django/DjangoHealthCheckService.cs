using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Events;
using Sentinel.Contracts.Health;
using Sentinel.Infrastructure.CtfMock;

namespace Sentinel.Monitor.Django;

public sealed class DjangoHealthCheckService : IDjangoHealthCheckService
{
    private readonly ICtfMockClient _ctfMockClient;
    private readonly IDjangoHealthMetrics _metrics;

    public DjangoHealthCheckService(
        ICtfMockClient ctfMockClient,
        IDjangoHealthMetrics? metrics = null)
    {
        _ctfMockClient = ctfMockClient;
        _metrics = metrics ?? NullDjangoHealthMetrics.Instance;
    }

    public async Task<DjangoHealthCheckResult> CheckAsync(
        CancellationToken cancellationToken = default)
    {
        DjangoHealthEvent healthEvent;
        CtfMockApiStatus apiStatus;

        try
        {
            healthEvent = await _ctfMockClient.GetDjangoHealthAsync(cancellationToken);
            apiStatus = await _ctfMockClient.GetApiStatusAsync(cancellationToken);
        }
        catch (Exception exception) when (exception is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
            _metrics.RecordFailure(exception);
            throw;
        }

        var alerts = BuildAdminAlertCandidates(healthEvent, apiStatus);
        var result = new DjangoHealthCheckResult(
            IsHealthy: alerts.Count == 0,
            HealthEvent: healthEvent,
            ApiStatus: apiStatus,
            AdminAlertCandidates: alerts);
        _metrics.Record(result);

        return result;
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
