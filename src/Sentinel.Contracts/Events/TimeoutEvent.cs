namespace Sentinel.Contracts.Events;

// TTL 만료, idle timeout, API timeout처럼 시간 기준으로 판단한 이벤트입니다.
public sealed record TimeoutEvent(
    string EventId,
    string InstanceId,
    TimeoutKind Kind,
    EventSeverity Severity,
    DateTimeOffset DetectedAt,
    DateTimeOffset? ExpiresAt,
    TimeSpan? IdleDuration);
