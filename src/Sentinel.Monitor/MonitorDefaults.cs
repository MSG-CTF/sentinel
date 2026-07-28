namespace Sentinel.Monitor;

public static class MonitorDefaults
{
    // 실제 worker 구현 전까지 감시 주기를 한 곳에서 공유합니다.
    public static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(30);

    public const string DjangoServerMonitorName = "django-server-monitor";
    public const string InstanceMonitorName = "instance-monitor";
}
