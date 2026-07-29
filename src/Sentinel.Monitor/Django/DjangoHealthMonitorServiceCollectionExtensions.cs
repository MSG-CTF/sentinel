using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace Sentinel.Monitor.Django;

public static class DjangoHealthMonitorServiceCollectionExtensions
{
    public static IServiceCollection AddDjangoHealthMonitor(
        this IServiceCollection services,
        IConfiguration configuration)
    {
        services.Configure<DjangoHealthMonitorOptions>(
            configuration.GetSection(DjangoHealthMonitorOptions.SectionName));
        services.AddSingleton<IDjangoHealthCheckService, DjangoHealthCheckService>();
        services.AddHostedService<DjangoHealthCheckWorker>();

        return services;
    }
}
