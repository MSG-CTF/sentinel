namespace Sentinel.Monitor.Django;

public interface IDjangoHealthMetrics
{
    void Record(DjangoHealthCheckResult result);
}
