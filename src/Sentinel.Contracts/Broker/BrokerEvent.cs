using Sentinel.Contracts.Events;

namespace Sentinel.Contracts.Broker;

public sealed record BrokerEvent(
    string EventId,
    string AccountId,
    BrokerEventType Type,
    EventSeverity Severity,
    string Message,
    DateTimeOffset OccurredAt);
