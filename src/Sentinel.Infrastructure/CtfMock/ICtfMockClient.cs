using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Broker;
using Sentinel.Contracts.Health;
using Sentinel.Contracts.Instances;
using Sentinel.Contracts.Scheduler;

namespace Sentinel.Infrastructure.CtfMock;

public interface ICtfMockClient
{
    Task<DjangoHealthEvent> GetDjangoHealthAsync(CancellationToken cancellationToken = default);

    Task<CtfMockApiStatus> GetApiStatusAsync(CancellationToken cancellationToken = default);

    Task<IReadOnlyList<MonitoredInstance>> GetInstancesAsync(CancellationToken cancellationToken = default);

    Task<HealthSignal> GetInstanceHealthAsync(
        string instanceId,
        CancellationToken cancellationToken = default);

    Task<IReadOnlyList<BrokerAccountStatus>> GetBrokerAccountsAsync(
        CancellationToken cancellationToken = default);

    Task<CtfMockPostResult> SendSchedulerSignalAsync(
        SchedulerSignal signal,
        CancellationToken cancellationToken = default);

    Task<CtfMockPostResult> SendAdminAlertAsync(
        AdminAlert alert,
        CancellationToken cancellationToken = default);
}
