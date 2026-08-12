using Prometheus;
using Sentinel.Contracts.Health;

namespace Sentinel.Monitor.Django;

public sealed class PrometheusDjangoHealthMetrics : IDjangoHealthMetrics
{
    private static readonly Gauge DjangoHealthCheckSuccessGauge = Metrics.CreateGauge(
        "sentinel_django_health_check_success",
        "Last Django health check execution result. 1 means the check completed, 0 means the check failed before a result was recorded.");

    private static readonly Gauge DjangoHealthStatusGauge = Metrics.CreateGauge(
        "sentinel_django_health_status",
        "Django health status by component. 1 means healthy, 0 means unhealthy.",
        new GaugeConfiguration
        {
            LabelNames = ["component"],
        });

    private static readonly Gauge DjangoApiStatusGauge = Metrics.CreateGauge(
        "sentinel_django_api_status",
        "Django API status by endpoint. 1 means healthy, 0 means unhealthy.",
        new GaugeConfiguration
        {
            LabelNames = ["api", "path"],
        });

    private static readonly Gauge AlertCandidatesGauge = Metrics.CreateGauge(
        "sentinel_django_admin_alert_candidates",
        "Current number of admin alert candidates created by the Django health check.",
        new GaugeConfiguration
        {
            LabelNames = ["source"],
        });

    private static readonly Counter AlertCandidatesTotalCounter = Metrics.CreateCounter(
        "sentinel_django_admin_alert_candidates_total",
        "Total number of admin alert candidates observed by the Django health check.",
        new CounterConfiguration
        {
            LabelNames = ["source"],
        });

    public void Record(DjangoHealthCheckResult result)
    {
        DjangoHealthCheckSuccessGauge.Set(1);
        DjangoHealthStatusGauge.WithLabels("django").Set(ToMetricValue(result.HealthEvent.Status));
        DjangoHealthStatusGauge.WithLabels("database").Set(ToMetricValue(result.HealthEvent.DatabaseStatus));
        DjangoHealthStatusGauge.WithLabels("cache").Set(ToMetricValue(result.HealthEvent.CacheStatus));

        foreach (var api in result.ApiStatus.Apis)
        {
            DjangoApiStatusGauge.WithLabels(api.Name, api.Path).Set(ToMetricValue(api.Status));
        }

        AlertCandidatesGauge
            .WithLabels(MonitorDefaults.DjangoServerMonitorName)
            .Set(result.AdminAlertCandidates.Count);

        if (result.AdminAlertCandidates.Count > 0)
        {
            AlertCandidatesTotalCounter
                .WithLabels(MonitorDefaults.DjangoServerMonitorName)
                .Inc(result.AdminAlertCandidates.Count);
        }
    }

    public void RecordFailure(Exception exception)
    {
        DjangoHealthCheckSuccessGauge.Set(0);
    }

    private static double ToMetricValue(HealthStatus status)
    {
        return status == HealthStatus.Healthy ? 1 : 0;
    }

    private static double ToMetricValue(string status)
    {
        return string.Equals(status, "healthy", StringComparison.OrdinalIgnoreCase) ? 1 : 0;
    }
}
