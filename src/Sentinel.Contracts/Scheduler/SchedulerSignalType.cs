namespace Sentinel.Contracts.Scheduler;

public enum SchedulerSignalType
{
    RestartInstance,
    StopInstance,
    ExtendInstanceTtl,
    QuarantineInstance
}
