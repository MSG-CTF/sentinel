using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;

namespace Sentinel.Monitor.Instances;

public static class InstanceInventoryRegistration
{
    public static IServiceCollection AddInstanceInventory(this IServiceCollection services, IConfiguration configuration)
    {
        services.AddOptions<InstanceInventoryOptions>().Bind(configuration.GetSection(InstanceInventoryOptions.SectionName))
            .Validate(o => o.IsValid(), "Invalid instance_monitor configuration").ValidateOnStart();
        services.AddHttpClient("instance_inventory", client => client.Timeout = Timeout.InfiniteTimeSpan)
            .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
            { AllowAutoRedirect = false, UseCookies = false, MaxConnectionsPerServer = 4,
                PooledConnectionLifetime = TimeSpan.FromMinutes(2) });
        services.AddSingleton(serviceProvider => new InstanceInventory(
            serviceProvider.GetRequiredService<IHttpClientFactory>().CreateClient("instance_inventory"),
            serviceProvider.GetRequiredService<IOptions<InstanceInventoryOptions>>(),
            challengeHeartbeats: serviceProvider.GetRequiredService<ChallengeHeartbeatStore>()));
        services.AddHostedService<InstanceInventoryWorker>();
        return services;
    }
}

public sealed class InstanceInventoryWorker(InstanceInventory inventory, IOptions<InstanceInventoryOptions> options,
    ILogger<InstanceInventoryWorker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!options.Value.Enabled) return;
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(options.Value.PollIntervalSeconds));
        do
        {
            try { await inventory.RefreshAsync(stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception) { logger.LogError("Instance inventory failed: {ExceptionType}", exception.GetType().Name); }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
