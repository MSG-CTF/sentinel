namespace Sentinel.Contracts.Scheduler;

// sentinel은 직접 복구하지 않고 scheduler가 실행할 작업 의도만 신호로 전달합니다.
public sealed record SchedulerSignal(
    string SignalId,
    SchedulerSignalType Type,
    string TargetId,
    string Reason,
    DateTimeOffset RequestedAt,
    IReadOnlyDictionary<string, string> Metadata);
