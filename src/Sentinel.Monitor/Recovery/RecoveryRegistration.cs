using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using Sentinel.Monitor.Platform;

namespace Sentinel.Monitor.Recovery;

public static class RecoveryRegistration
{
    public static IServiceCollection AddPlatformRecovery(this IServiceCollection services, IConfiguration configuration)
    {
        var platform = configuration.GetSection(PlatformOptions.SectionName).Get<PlatformOptions>() ?? new();
        services.AddOptions<RecoveryOptions>().Bind(configuration.GetSection(RecoveryOptions.SectionName))
            .Validate(options => options.IsValid(platform), "Invalid local recovery configuration").ValidateOnStart();
        services.AddSingleton<RecoveryCoordinator>();
        services.AddSingleton<RecoveryAuthentication>();
        services.AddHostedService<RecoveryWorker>();
        return services;
    }
}

public sealed class RecoveryAuthentication
{
    private readonly byte[]? _tokenHash;
    public RecoveryAuthentication(IOptions<RecoveryOptions> options)
    {
        if (!options.Value.Enabled) return;
        var token = File.ReadAllText(options.Value.TokenFile).Trim();
        if (token.Length != 64 || token.Any(character => !Uri.IsHexDigit(character)))
            throw new InvalidOperationException("Recovery executor token is not configured");
        _tokenHash = SHA256.HashData(Encoding.UTF8.GetBytes(token));
    }

    public bool Authorize(string header) => _tokenHash is not null && header.StartsWith("Bearer ", StringComparison.Ordinal)
        && header.Length == 71 && CryptographicOperations.FixedTimeEquals(_tokenHash,
            SHA256.HashData(Encoding.UTF8.GetBytes(header[7..])));
}

public sealed class RecoveryWorker(PlatformMonitor monitor, RecoveryCoordinator recovery, RecoveryAuthentication authentication) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _ = authentication; // Validate enabled credentials during startup.
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        do { recovery.Observe(monitor.Snapshot()); }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
