using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace Sentinel.Infrastructure.CtfMock;

public static class CtfMockServiceCollectionExtensions
{
    public static IServiceCollection AddCtfMockClient(
        this IServiceCollection services,
        IConfiguration configuration)
    {
        services.Configure<CtfMockOptions>(
            configuration.GetSection(CtfMockOptions.SectionName));

        services.AddHttpClient<ICtfMockClient, CtfMockClient>((provider, client) =>
        {
            var options = provider.GetRequiredService<IOptions<CtfMockOptions>>().Value;
            client.BaseAddress = options.BaseUrl;
            client.Timeout = options.Timeout;
        });

        return services;
    }
}
