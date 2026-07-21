namespace Sentinel.Contracts.Health;

public sealed record HealthResponse(
    string Service,
    HealthStatus Status,
    DateTimeOffset CheckedAt);
