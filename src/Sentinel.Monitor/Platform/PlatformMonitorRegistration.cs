using System.Net;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Sentinel.Monitor.Platform;

public static class PlatformMonitorRegistration
{
    public static IServiceCollection AddPlatformMonitor(this IServiceCollection services, IConfiguration configuration)
    {
        services.AddOptions<PlatformOptions>().Bind(configuration.GetSection(PlatformOptions.SectionName))
            .Validate(options => options.IsValid(), "Invalid platform_monitor profile")
            .ValidateOnStart();
        services.AddHttpClient<PlatformProbeClient>(client => client.Timeout = Timeout.InfiniteTimeSpan)
            .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
            {
                AllowAutoRedirect = false,
                UseCookies = false,
                PooledConnectionLifetime = TimeSpan.FromMinutes(2),
                MaxConnectionsPerServer = 4,
                AutomaticDecompression = DecompressionMethods.All,
            });
        services.AddSingleton<PlatformMonitor>();
        services.AddHostedService<PlatformMonitorWorker>();
        return services;
    }
}

public sealed class PlatformMonitorWorker(
    PlatformMonitor monitor, IOptions<PlatformOptions> options, ILogger<PlatformMonitorWorker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(options.Value.PollIntervalSeconds));
        do
        {
            try { await monitor.RefreshAsync(stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception)
            {
                // 외부 응답 원문이나 예외 메시지는 로그에 싣지 않는다
                logger.LogError("Platform refresh failed. exception_type={ExceptionType}", exception.GetType().Name);
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
