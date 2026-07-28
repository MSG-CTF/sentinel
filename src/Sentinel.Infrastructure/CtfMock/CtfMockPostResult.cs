namespace Sentinel.Infrastructure.CtfMock;

public sealed record CtfMockPostResult(
    bool Accepted,
    int? Received,
    string? SignalId,
    string? AlertId,
    DateTimeOffset? ReceivedAt);
