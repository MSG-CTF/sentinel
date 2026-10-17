namespace Sentinel.Monitor.Django;

public sealed class NullDjangoHealthMetrics : IDjangoHealthMetrics
{
    public static readonly NullDjangoHealthMetrics Instance = new();

    private NullDjangoHealthMetrics()
    {
    }

    public void Record(DjangoHealthCheckResult result)
    {
    }

    public void RecordFailure(Exception exception)
    {
    }
}
