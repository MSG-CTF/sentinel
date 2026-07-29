namespace Sentinel.Monitor;

public static class MonitorDefaults
{
    // 별도 설정이 없을 때 worker들이 공통으로 사용하는 기본 감시 주기입니다.
    public static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(30);

    public const string DjangoServerMonitorName = "django-server-monitor";
    public const string InstanceMonitorName = "instance-monitor";
}
