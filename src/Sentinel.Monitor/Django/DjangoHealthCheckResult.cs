using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Health;
using Sentinel.Infrastructure.CtfMock;

namespace Sentinel.Monitor.Django;

public sealed record DjangoHealthCheckResult(
    bool IsHealthy,
    DjangoHealthEvent HealthEvent,
    CtfMockApiStatus ApiStatus,
    IReadOnlyList<AdminAlert> AdminAlertCandidates);
