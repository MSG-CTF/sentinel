namespace Sentinel.Monitor.Django;

public interface IDjangoHealthCheckService
{
    Task<DjangoHealthCheckResult> CheckAsync(CancellationToken cancellationToken = default);
}
