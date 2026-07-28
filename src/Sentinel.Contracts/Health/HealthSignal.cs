namespace Sentinel.Contracts.Health;

// Monitor가 감지한 health 결과를 scheduler/admin 판단 흐름으로 넘길 때 쓰는 공통 신호입니다.
public sealed record HealthSignal(
    string TargetId,
    MonitoredTargetType TargetType,
    HealthStatus Status,
    string Reason,
    DateTimeOffset CheckedAt,
    IReadOnlyDictionary<string, string> Metadata);
