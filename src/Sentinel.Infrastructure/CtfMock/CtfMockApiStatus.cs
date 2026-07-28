namespace Sentinel.Infrastructure.CtfMock;

public sealed record CtfMockApiStatus(
    string Status,
    string? Reason,
    IReadOnlyList<CtfMockApiStatusItem> Apis,
    DateTimeOffset CheckedAt);

public sealed record CtfMockApiStatusItem(
    string Name,
    string Path,
    string Status,
    int LatencyMs);
