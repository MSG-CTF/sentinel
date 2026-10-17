using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Prometheus;

namespace Sentinel.Monitor.Django;

public static class DjangoHealthMonitorServiceCollectionExtensions
{
    public static IServiceCollection AddDjangoHealthMonitor(
        this IServiceCollection services,
        IConfiguration configuration)
    {
        services.TryAddSingleton(_ => Metrics.NewCustomRegistry());
        services.Configure<DjangoHealthMonitorOptions>(
            configuration.GetSection(DjangoHealthMonitorOptions.SectionName));
        services.AddSingleton<IDjangoHealthMetrics, PrometheusDjangoHealthMetrics>();
        services.AddSingleton<IDjangoHealthCheckService, DjangoHealthCheckService>();
        services.AddHostedService<DjangoHealthCheckWorker>();

        return services;
    }
}
