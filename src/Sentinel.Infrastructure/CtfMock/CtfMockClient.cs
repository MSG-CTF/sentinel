using System.Net.Http.Json;
using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Broker;
using Sentinel.Contracts.Health;
using Sentinel.Contracts.Instances;
using Sentinel.Contracts.Scheduler;

namespace Sentinel.Infrastructure.CtfMock;

public sealed class CtfMockClient : ICtfMockClient
{
    private readonly HttpClient _httpClient;

    public CtfMockClient(HttpClient httpClient)
    {
        _httpClient = httpClient;
    }

    public async Task<DjangoHealthEvent> GetDjangoHealthAsync(
        CancellationToken cancellationToken = default)
    {
        var dto = await GetJsonAsync<DjangoHealthDto>("/health", cancellationToken);

        return new DjangoHealthEvent(
            Status: MapHealthStatus(dto.Status),
            DatabaseStatus: MapHealthStatus(dto.Database),
            CacheStatus: MapHealthStatus(dto.Cache),
            Reason: dto.Reason ?? dto.Status,
            CheckedAt: dto.CheckedAt);
    }

    public async Task<CtfMockApiStatus> GetApiStatusAsync(
        CancellationToken cancellationToken = default)
    {
        var dto = await GetJsonAsync<ApiStatusDto>("/api/status", cancellationToken);

        return new CtfMockApiStatus(
            Status: dto.Status,
            Reason: dto.Reason,
            Apis: dto.Apis,
            CheckedAt: dto.CheckedAt);
    }

    public async Task<IReadOnlyList<MonitoredInstance>> GetInstancesAsync(
        CancellationToken cancellationToken = default)
    {
        var dto = await GetJsonAsync<InstancesDto>("/api/instances", cancellationToken);

        return dto.Instances
            .Select(instance => new MonitoredInstance(
                InstanceId: instance.InstanceId,
                TeamId: instance.TeamId,
                ChallengeId: instance.ChallengeId,
                Status: MapInstanceStatus(instance.Status),
                CreatedAt: instance.CreatedAt,
                ExpiresAt: instance.ExpiresAt,
                LastActivityAt: instance.LastActivityAt))
            .ToArray();
    }

    public async Task<HealthSignal> GetInstanceHealthAsync(
        string instanceId,
        CancellationToken cancellationToken = default)
    {
        var dto = await GetJsonAsync<InstanceHealthDto>(
            $"/api/instances/{Uri.EscapeDataString(instanceId)}/health",
            cancellationToken);

        return new HealthSignal(
            TargetId: dto.InstanceId,
            TargetType: MonitoredTargetType.Instance,
            Status: MapHealthStatus(dto.Status),
            Reason: dto.Reason ?? dto.Status,
            CheckedAt: dto.CheckedAt,
            Metadata: new Dictionary<string, string>
            {
                ["latencyMs"] = dto.LatencyMs?.ToString() ?? string.Empty,
            });
    }

    public async Task<IReadOnlyList<BrokerAccountStatus>> GetBrokerAccountsAsync(
        CancellationToken cancellationToken = default)
    {
        var accounts = await GetJsonAsync<IReadOnlyList<BrokerAccountDto>>(
            "/api/broker/accounts",
            cancellationToken);

        return accounts
            .Select(account => new BrokerAccountStatus(
                AccountId: account.AccountId,
                State: MapBrokerState(account.Status),
                RemainingQuota: account.Quota?.CpuLimit - account.Quota?.CpuUsed,
                BudgetUsed: account.Budget?.UsedUsd,
                BudgetLimit: account.Budget?.LimitUsd,
                CheckedAt: DateTimeOffset.UtcNow))
            .ToArray();
    }

    public Task<CtfMockPostResult> SendSchedulerSignalAsync(
        SchedulerSignal signal,
        CancellationToken cancellationToken = default)
    {
        return PostJsonAsync("/api/scheduler/signals", signal, cancellationToken);
    }

    public Task<CtfMockPostResult> SendAdminAlertAsync(
        AdminAlert alert,
        CancellationToken cancellationToken = default)
    {
        return PostJsonAsync("/api/admin/alerts", alert, cancellationToken);
    }

    private async Task<T> GetJsonAsync<T>(string path, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.GetAsync(path, cancellationToken);
        var payload = await response.Content.ReadFromJsonAsync<T>(
            CtfMockJson.Options,
            cancellationToken);

        return payload ?? throw new InvalidOperationException(
            $"ctf-mock response body is empty. path={path}");
    }

    private async Task<CtfMockPostResult> PostJsonAsync<T>(
        string path,
        T payload,
        CancellationToken cancellationToken)
    {
        using var response = await _httpClient.PostAsJsonAsync(
            path,
            payload,
            CtfMockJson.Options,
            cancellationToken);
        response.EnsureSuccessStatusCode();

        var result = await response.Content.ReadFromJsonAsync<CtfMockPostResult>(
            CtfMockJson.Options,
            cancellationToken);

        return result ?? throw new InvalidOperationException(
            $"ctf-mock response body is empty. path={path}");
    }

    private static HealthStatus MapHealthStatus(string status)
    {
        return status.ToLowerInvariant() switch
        {
            "healthy" => HealthStatus.Healthy,
            "degraded" => HealthStatus.Degraded,
            _ => HealthStatus.Unhealthy,
        };
    }

    private static InstanceStatus MapInstanceStatus(string status)
    {
        return status.ToLowerInvariant() switch
        {
            "running" => InstanceStatus.Running,
            "healthy" => InstanceStatus.Healthy,
            "expired" => InstanceStatus.Expired,
            "idle" => InstanceStatus.Idle,
            "unhealthy" or "timeout" => InstanceStatus.Unhealthy,
            _ => InstanceStatus.Unknown,
        };
    }

    private static BrokerAccountState MapBrokerState(string status)
    {
        return status.ToLowerInvariant() switch
        {
            "available" or "healthy" => BrokerAccountState.Healthy,
            "quota_exhausted" => BrokerAccountState.QuotaExhausted,
            "budget_risk" => BrokerAccountState.BudgetRisk,
            "broker_unavailable" or "provider_failure" => BrokerAccountState.BrokerUnavailable,
            _ => BrokerAccountState.Unknown,
        };
    }

    private sealed record DjangoHealthDto(
        string Status,
        string Database,
        string Cache,
        string? Reason,
        DateTimeOffset CheckedAt);

    private sealed record ApiStatusDto(
        string Status,
        string? Reason,
        IReadOnlyList<CtfMockApiStatusItem> Apis,
        DateTimeOffset CheckedAt);

    private sealed record InstancesDto(IReadOnlyList<InstanceDto> Instances);

    private sealed record InstanceDto(
        string InstanceId,
        string TeamId,
        string ChallengeId,
        string Status,
        DateTimeOffset CreatedAt,
        DateTimeOffset? ExpiresAt,
        DateTimeOffset? LastActivityAt);

    private sealed record InstanceHealthDto(
        string InstanceId,
        string Status,
        string? Reason,
        int? LatencyMs,
        DateTimeOffset CheckedAt);

    private sealed record BrokerAccountDto(
        string AccountId,
        string Status,
        BrokerQuotaDto? Quota,
        BrokerBudgetDto? Budget);

    private sealed record BrokerQuotaDto(int CpuUsed, int CpuLimit);

    private sealed record BrokerBudgetDto(decimal UsedUsd, decimal LimitUsd);
}
