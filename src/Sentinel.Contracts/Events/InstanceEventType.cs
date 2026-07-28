namespace Sentinel.Contracts.Events;

public enum InstanceEventType
{
    HealthCheckFailed,
    HealthCheckRecovered,
    TtlExpired,
    IdleTimeout,
    RestartRequested
}
