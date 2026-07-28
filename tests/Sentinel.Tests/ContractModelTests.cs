using Sentinel.Contracts.Alerts;
using Sentinel.Contracts.Broker;
using Sentinel.Contracts.Events;
using Sentinel.Contracts.Health;
using Sentinel.Contracts.Instances;
using Sentinel.Contracts.Providers;
using Sentinel.Contracts.Scheduler;
using Xunit;

namespace Sentinel.Tests;

public sealed class ContractModelTests
{
    [Fact]
    public void SchedulerSignal_ShouldKeepRecoveryRequestFields()
    {
        var requestedAt = DateTimeOffset.Parse("2026-07-28T01:00:00Z");
        var signal = new SchedulerSignal(
            SignalId: "signal-1",
            Type: SchedulerSignalType.RestartInstance,
            TargetId: "instance-web-1",
            Reason: "instance_timeout",
            RequestedAt: requestedAt,
            Metadata: new Dictionary<string, string>
            {
                ["teamId"] = "team-1",
                ["challengeId"] = "web-100",
            });

        Assert.Equal("signal-1", signal.SignalId);
        Assert.Equal(SchedulerSignalType.RestartInstance, signal.Type);
        Assert.Equal("instance-web-1", signal.TargetId);
        Assert.Equal("instance_timeout", signal.Reason);
        Assert.Equal(requestedAt, signal.RequestedAt);
        Assert.Equal("team-1", signal.Metadata["teamId"]);
    }

    [Fact]
    public void AdminAlert_ShouldKeepHumanReadableAlertFields()
    {
        var createdAt = DateTimeOffset.Parse("2026-07-28T01:10:00Z");
        var alert = new AdminAlert(
            AlertId: "alert-1",
            Severity: EventSeverity.Critical,
            Title: "Django database unavailable",
            Message: "본 CTF 서버 database health check가 실패했습니다.",
            Source: "django-server-monitor",
            CreatedAt: createdAt,
            Metadata: new Dictionary<string, string>
            {
                ["scenario"] = "django_db_down",
            });

        Assert.Equal(EventSeverity.Critical, alert.Severity);
        Assert.Equal("Django database unavailable", alert.Title);
        Assert.Equal("django-server-monitor", alert.Source);
        Assert.Equal(createdAt, alert.CreatedAt);
        Assert.Equal("django_db_down", alert.Metadata["scenario"]);
    }

    [Fact]
    public void MonitoringContracts_ShouldRepresentDjangoInstanceAndBrokerState()
    {
        var checkedAt = DateTimeOffset.Parse("2026-07-28T02:00:00Z");
        var djangoEvent = new DjangoHealthEvent(
            Status: HealthStatus.Unhealthy,
            DatabaseStatus: HealthStatus.Unhealthy,
            CacheStatus: HealthStatus.Healthy,
            Reason: "database_unavailable",
            CheckedAt: checkedAt);

        var instance = new MonitoredInstance(
            InstanceId: "instance-pwn-1",
            TeamId: "team-7",
            ChallengeId: "pwn-200",
            Status: InstanceStatus.Unhealthy,
            CreatedAt: checkedAt.AddMinutes(-30),
            ExpiresAt: checkedAt.AddMinutes(30),
            LastActivityAt: checkedAt.AddMinutes(-20));

        var broker = new BrokerAccountStatus(
            AccountId: "aws-main",
            State: BrokerAccountState.BudgetRisk,
            RemainingQuota: 3,
            BudgetUsed: 90000m,
            BudgetLimit: 100000m,
            CheckedAt: checkedAt);

        Assert.Equal(HealthStatus.Unhealthy, djangoEvent.DatabaseStatus);
        Assert.Equal("database_unavailable", djangoEvent.Reason);
        Assert.Equal(InstanceStatus.Unhealthy, instance.Status);
        Assert.Equal("team-7", instance.TeamId);
        Assert.Equal(BrokerAccountState.BudgetRisk, broker.State);
        Assert.Equal(100000m, broker.BudgetLimit);
    }

    [Fact]
    public void EventContracts_ShouldDistinguishInstanceTimeoutBrokerAndProviderEvents()
    {
        var occurredAt = DateTimeOffset.Parse("2026-07-28T03:00:00Z");
        var instanceEvent = new InstanceEvent(
            EventId: "event-instance-1",
            InstanceId: "instance-rev-1",
            Type: InstanceEventType.HealthCheckFailed,
            Severity: EventSeverity.Warning,
            Message: "instance health check failed",
            OccurredAt: occurredAt,
            Metadata: new Dictionary<string, string>());

        var timeoutEvent = new TimeoutEvent(
            EventId: "event-timeout-1",
            InstanceId: "instance-rev-1",
            Kind: TimeoutKind.IdleTimeout,
            Severity: EventSeverity.Warning,
            DetectedAt: occurredAt,
            ExpiresAt: null,
            IdleDuration: TimeSpan.FromMinutes(20));

        var brokerEvent = new BrokerEvent(
            EventId: "event-broker-1",
            AccountId: "aws-main",
            Type: BrokerEventType.QuotaExhausted,
            Severity: EventSeverity.Critical,
            Message: "quota exhausted",
            OccurredAt: occurredAt);

        var providerEvent = new ProviderEvent(
            EventId: "event-provider-1",
            ProviderName: "aws",
            Type: ProviderEventType.ProviderFailure,
            Severity: EventSeverity.Critical,
            Message: "provider unavailable",
            OccurredAt: occurredAt);

        Assert.Equal(InstanceEventType.HealthCheckFailed, instanceEvent.Type);
        Assert.Equal(TimeoutKind.IdleTimeout, timeoutEvent.Kind);
        Assert.Equal(BrokerEventType.QuotaExhausted, brokerEvent.Type);
        Assert.Equal(ProviderEventType.ProviderFailure, providerEvent.Type);
    }
}
