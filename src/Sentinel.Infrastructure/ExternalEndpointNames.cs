namespace Sentinel.Infrastructure;

public static class ExternalEndpointNames
{
    // 이후 IHttpClientFactory 등록 시 이름이 흩어지지 않도록 미리 고정합니다.
    public const string CtfServer = "ctf-server";
    public const string Scheduler = "scheduler";
    public const string AdminAlert = "admin-alert";
}
