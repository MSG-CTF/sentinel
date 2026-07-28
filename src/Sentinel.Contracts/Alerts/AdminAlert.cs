using Sentinel.Contracts.Events;

namespace Sentinel.Contracts.Alerts;

// 자동 처리보다 운영자 확인이 필요한 상황을 admin 채널로 전달할 때 사용합니다.
public sealed record AdminAlert(
    string AlertId,
    EventSeverity Severity,
    string Title,
    string Message,
    string Source,
    DateTimeOffset CreatedAt,
    IReadOnlyDictionary<string, string> Metadata);
