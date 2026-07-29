using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sentinel.Infrastructure.CtfMock;

public static class CtfMockJson
{
    public static readonly JsonSerializerOptions Options = CreateOptions();

    private static JsonSerializerOptions CreateOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        };

        options.Converters.Add(new JsonStringEnumConverter());

        return options;
    }
}
