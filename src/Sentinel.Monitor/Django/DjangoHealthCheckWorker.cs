using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Django;

public sealed class DjangoHealthCheckWorker : BackgroundService
{
    private readonly IDjangoHealthCheckService _healthCheckService;
    private readonly DjangoHealthMonitorOptions _options;
    private readonly ILogger<DjangoHealthCheckWorker> _logger;

    public DjangoHealthCheckWorker(
        IDjangoHealthCheckService healthCheckService,
        IOptions<DjangoHealthMonitorOptions> options,
        ILogger<DjangoHealthCheckWorker> logger)
    {
        _healthCheckService = healthCheckService;
        _options = options.Value;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(_options.PollInterval);

        while (!stoppingToken.IsCancellationRequested)
        {
            await CheckOnceAsync(stoppingToken);

            if (!await timer.WaitForNextTickAsync(stoppingToken))
            {
                break;
            }
        }
    }

    private async Task CheckOnceAsync(CancellationToken cancellationToken)
    {
        try
        {
            var result = await _healthCheckService.CheckAsync(cancellationToken);

            if (result.IsHealthy)
            {
                _logger.LogInformation("Django health check passed.");
                return;
            }

            // 이슈 #7에서 실제 admin alert 전송을 붙일 예정이라 여기서는 후보만 로그로 남깁니다.
            foreach (var alert in result.AdminAlertCandidates)
            {
                _logger.LogWarning(
                    "Django health alert candidate created. alertId={AlertId}, reason={Reason}",
                    alert.AlertId,
                    alert.Metadata.GetValueOrDefault("reason"));
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception)
        {
            _logger.LogError(exception, "Django health check failed unexpectedly.");
        }
    }
}
