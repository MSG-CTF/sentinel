namespace Sentinel.Contracts.Health;

public sealed record DjangoHealthEvent(
    HealthStatus Status,
    HealthStatus DatabaseStatus,
    HealthStatus CacheStatus,
    string Reason,
    DateTimeOffset CheckedAt);
