namespace Sentinel.Infrastructure.CtfMock;

public sealed class CtfMockOptions
{
    public const string SectionName = "CtfMock";

    public Uri BaseUrl { get; init; } = new("http://localhost:8000");

    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(5);
}
