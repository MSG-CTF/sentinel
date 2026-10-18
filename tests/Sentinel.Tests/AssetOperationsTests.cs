using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.Tests;

public sealed class AssetOperationsTests : IDisposable
{
    private readonly string _file=Path.Combine(Path.GetTempPath(),"sentinel-ops-"+Guid.NewGuid()+".json");
    private readonly FakeSsh _ssh=new();
    private static readonly ManagedAsset Bot=new("bot","example.com",22,"sentinel-ops",Convert.ToBase64String(new byte[32]),"example.service","SENTINEL_SSH_TEST_B64");
    private AssetOperations Create()=>new([Bot],_ssh,_file);
    private PlatformSnapshot Sample(string status="DOWN",string service="inactive",int age=0)=>new("test","observe_only",DateTimeOffset.UtcNow,DateTimeOffset.UtcNow,15,3,true,
        [new("bot","Bot","DISCORD","CONTROL","service_heartbeat",null,true,status,"SERVICE_INACTIVE",DateTimeOffset.UtcNow.AddSeconds(-age),null,null,new Dictionary<string,string>{{"service_state",service}})]);
    [Fact]
    public async Task OnlyConfiguredTargetsAndFixedActionsCanReachSsh()
    {
        var ops=Create(); var id=Guid.NewGuid().ToString();
        Assert.Equal("REJECTED",(await ops.ExecuteAsync("unknown",new("status",id),"admin",Sample())).Status);
        Assert.Equal("REJECTED",(await ops.ExecuteAsync("bot",new("status; whoami",id),"admin",Sample())).Status);
        Assert.Equal("REJECTED",(await ops.ExecuteAsync("bot",new("status","bad"),"admin",Sample())).Status);
        Assert.Equal(0,_ssh.Calls);
    }
    [Theory]
    [InlineData("HEALTHY","active",0)]
    [InlineData("DOWN","inactive",151)]
    [InlineData("STALE","inactive",1)]
    [InlineData("DOWN","activating",0)]
    public async Task RecoveryRequiresFreshStoppedService(string status,string service,int age)
    {
        var result=await Create().ExecuteAsync("bot",new("start",Guid.NewGuid().ToString(),"bot"),"admin",Sample(status,service,age));
        Assert.Equal("REJECTED",result.Status); Assert.Equal(0,_ssh.Calls);
    }
    [Fact]
    public async Task RuntimeDatabaseFailureCannotStartOrRestartAnActiveService()
    {
        var runtime = Bot with { AssetId = "runtime", Service = "secure-provisioner.service" };
        var ops = new AssetOperations([runtime], _ssh, _file);
        var sample = Sample("HEALTHY", "active") with { Targets = [
            new("runtime", "Runtime", "RUNTIME", "RUNTIME", "service_heartbeat", null, true, "HEALTHY", "SERVICE_ACTIVE",
                DateTimeOffset.UtcNow, null, null, new Dictionary<string,string> { ["service_state"] = "active" }),
            new("runtime-api", "Runtime API", "RUNTIME", "RUNTIME", "runtime_healthz", "https://runtime.invalid/internal/v1/healthz", true,
                "DOWN", "RUNTIME_DATABASE_DOWN", DateTimeOffset.UtcNow, 503, 10,
                new Dictionary<string,string> { ["database"] = "down" })] };
        var result = await ops.ExecuteAsync("runtime", new("start", Guid.NewGuid().ToString(), "runtime"), "admin", sample);
        Assert.Equal("FRESH_STOPPED_SERVICE_REQUIRED", result.Code);
        Assert.Equal(0, _ssh.Calls);
        result = await ops.ExecuteAsync("runtime-api", new("start", Guid.NewGuid().ToString(), "runtime-api"), "admin", sample);
        Assert.Equal("SSH_NOT_CONFIGURED", result.Code);
        Assert.Equal(0, _ssh.Calls);
    }
    [Fact]
    public async Task DuplicateRecoveryAcrossRestartCannotExecuteAgain()
    {
        var request=new AssetActionRequest("start",Guid.NewGuid().ToString(),"bot");
        var ops=Create(); var result=await ops.ExecuteAsync("bot",request,"admin",Sample());
        Assert.Equal("SUCCEEDED",result.Status); Assert.Equal("START_REQUESTED_AWAITING_HEARTBEAT",result.Code);
        Assert.Equal(result,await ops.ExecuteAsync("bot",request,"admin",Sample()));
        Assert.Equal(result,await Create().ExecuteAsync("bot",request,"admin",Sample())); Assert.Equal(1,_ssh.Calls);
        Assert.Equal("ACTION_COOLDOWN",(await ops.ExecuteAsync("bot",request with {RequestId=Guid.NewGuid().ToString()},"admin",Sample())).Code);
    }
    [Fact]
    public async Task UnconfirmedExecutionIsRecordedWithoutExposingErrorsOrRetrying()
    {
        _ssh.Fail=true;var request=new AssetActionRequest("start",Guid.NewGuid().ToString(),"bot");
        var result=await Create().ExecuteAsync("bot",request,"admin",Sample()); Assert.Equal("UNKNOWN",result.Status);
        Assert.DoesNotContain("private",result.Output); await Create().ExecuteAsync("bot",request,"admin",Sample()); Assert.Equal(1,_ssh.Calls);
    }
    [Fact]
    public async Task UnwritableAuditAndMissingConfirmationBlockMutation()
    {
        var ops=Create(); Assert.Equal("REJECTED",(await ops.ExecuteAsync("bot",new("start",Guid.NewGuid().ToString()),"admin",Sample())).Status);
        File.WriteAllText(_file,"corrupt"); Assert.Equal("REJECTED",(await Create().ExecuteAsync("bot",new("start",Guid.NewGuid().ToString(),"bot"),"admin",Sample())).Status);
        Assert.Equal(0,_ssh.Calls);
    }
    [Fact]
    public void HostKeyAndCredentialReferencesAreRequired()
    {
        Assert.True(AssetOperations.Valid(Bot)); Assert.False(AssetOperations.Valid(Bot with {HostKeySha256=""}));
        Assert.False(AssetOperations.Valid(Bot with {Host="host; command"})); Assert.False(AssetOperations.Valid(Bot with {Service="unit; command.service"}));
    }
    [Theory]
    [InlineData("docker:msg-dev-backend-1", true)]
    [InlineData("docker:--privileged", false)]
    [InlineData("docker:backend; id", false)]
    [InlineData("docker:../backend", false)]
    [InlineData("docker:", false)]
    public void ContainerReferencesRemainFixedDescriptors(string service, bool valid) =>
        Assert.Equal(valid, AssetOperations.Valid(Bot with { Service = service }));
    private sealed class FakeSsh:IAssetSshClient
    { public int Calls;public bool Fail;public Task<SshActionOutput> RunAsync(ManagedAsset asset,string action,CancellationToken token){Calls++;if(Fail)throw new IOException("private error");return Task.FromResult(new SshActionOutput(0,"{\"state\":\"active\"}"));} }
    public void Dispose(){if(File.Exists(_file))File.Delete(_file);}
}
