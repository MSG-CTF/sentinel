namespace Sentinel.Monitor.Django;

public sealed class DjangoHealthMonitorOptions
{
    public const string SectionName = "DjangoHealthMonitor";

    public TimeSpan PollInterval { get; init; } = MonitorDefaults.PollInterval;
}
