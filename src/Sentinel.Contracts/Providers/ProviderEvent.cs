using Sentinel.Contracts.Events;

namespace Sentinel.Contracts.Providers;

public sealed record ProviderEvent(
    string EventId,
    string ProviderName,
    ProviderEventType Type,
    EventSeverity Severity,
    string Message,
    DateTimeOffset OccurredAt);
