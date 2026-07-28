namespace Sentinel.Contracts.Events;

// 문제 인스턴스 단위에서 발생한 감지 결과를 event store에 남기기 위한 계약입니다.
public sealed record InstanceEvent(
    string EventId,
    string InstanceId,
    InstanceEventType Type,
    EventSeverity Severity,
    string Message,
    DateTimeOffset OccurredAt,
    IReadOnlyDictionary<string, string> Metadata);
