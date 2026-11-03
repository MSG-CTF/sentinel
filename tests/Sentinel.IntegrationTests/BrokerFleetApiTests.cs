using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using Sentinel.Api;
using Sentinel.Monitor.Platform;
using Xunit;

namespace Sentinel.IntegrationTests;

public sealed class BrokerFleetApiTests
{
    [Fact]
    public async Task PublisherAndObserverHaveSeparateScopesAndOnlyCompleteVersionedBatchesAffectTheLiveCatalog()
    {
        var directory=Path.Combine(Path.GetTempPath(),"fleet-api-"+Guid.NewGuid());Directory.CreateDirectory(directory);
        var authBefore=Environment.GetEnvironmentVariable("SENTINEL_AUTH_FILE");
        var authFile=Path.Combine(directory,"auth.json");var salt=RandomNumberGenerator.GetBytes(16);
        var pubName="FLEET_API_PUB_"+Guid.NewGuid().ToString("N");var obsName="FLEET_API_OBS_"+Guid.NewGuid().ToString("N");
        var publisher=new string('p',48);var observer=new string('o',48);
        await File.WriteAllTextAsync(authFile,JsonSerializer.Serialize(new AdminAccess {Enabled=true,Username="admin",
            PasswordHash="pbkdf2-sha256$600000$"+Convert.ToBase64String(salt)+"$"+Convert.ToBase64String(Rfc2898DeriveBytes.Pbkdf2("fixture-password",salt,600000,HashAlgorithmName.SHA256,32)),ReadinessToken=new string('r',48)}));
        Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE",authFile);Environment.SetEnvironmentVariable(pubName,publisher);Environment.SetEnvironmentVariable(obsName,observer);
        var fleet=new BrokerFleetStore(Options.Create(new BrokerFleetOptions {Enabled=true,StateFile=Path.Combine(directory,"fleet.json"),PublisherTokenEnv=pubName,ObserverTokenEnv=obsName}));
        var snake=new JsonSerializerOptions {PropertyNamingPolicy=JsonNamingPolicy.SnakeCaseLower};
        try
        {
            await using var factory=new WebApplicationFactory<Program>().WithWebHostBuilder(builder=>builder.ConfigureServices(services=>
            {
                services.RemoveAll<IHostedService>();services.RemoveAll<BrokerFleetStore>();services.AddSingleton(fleet);
                services.PostConfigure<PlatformOptions>(options=>options.Targets=[new() {TargetId="broker-fleet",Name="Fleet",Role="BROKER",Track="CONTROL",Kind="broker_fleet"}]);
            }));
            using var client=factory.CreateClient(new() {BaseAddress=new("https://localhost"),AllowAutoRedirect=false});
            var node=new FleetNode(Guid.NewGuid(),Guid.NewGuid(),"node","project","https://10.10.0.2:6443");
            var windows=new FleetWindows(Guid.NewGuid(),"windows","10.10.0.3",3389);
            var roster=new FleetRoster(1,DateTimeOffset.UtcNow,true,[node],[windows]);
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.PostAsJsonAsync("/ingest/fleet/roster",roster,snake)).StatusCode);
            client.DefaultRequestHeaders.Authorization=new("Bearer",publisher);
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.GetAsync("/api/fleet")).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.GetAsync("/api/status")).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.GetAsync("/ingest/fleet/desired")).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest,(await client.PostAsJsonAsync("/ingest/fleet/roster",roster)).StatusCode);
            var extra=JsonSerializer.Deserialize<Dictionary<string,JsonElement>>(JsonSerializer.Serialize(roster,snake))!;
            extra["extra_credentials"]=JsonSerializer.SerializeToElement("not-allowed");
            Assert.Equal(HttpStatusCode.BadRequest,(await client.PostAsJsonAsync("/ingest/fleet/roster",extra,snake)).StatusCode);
            var published=await client.PostAsJsonAsync("/ingest/fleet/roster",roster,snake);Assert.Equal(HttpStatusCode.OK,published.StatusCode);
            var version=(await published.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("roster_version").GetString()!;
            Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync("/ingest/fleet/roster",roster,snake)).StatusCode);
            var nodeReading=new ClusterHeartbeat(DateTimeOffset.UtcNow,node.NodeUid!.ToString()!,"ok",1,1,1,0,0,0,0);
            var rdp=new FleetRdp(DateTimeOffset.UtcNow,true);
            var batch=new FleetBatch(1,version,DateTimeOffset.UtcNow,[new(node.ResourceTargetId,nodeReading),new(windows.ResourceTargetId,Windows:rdp)]);
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.PostAsJsonAsync("/ingest/fleet/observations",batch,snake)).StatusCode);
            client.DefaultRequestHeaders.Authorization=new("Bearer",observer);
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.PostAsJsonAsync("/ingest/fleet/roster",roster,snake)).StatusCode);
            Assert.Equal(HttpStatusCode.OK,(await client.GetAsync("/ingest/fleet/desired")).StatusCode);
            Assert.Equal(HttpStatusCode.Unauthorized,(await client.PostAsJsonAsync("/ingest/cluster/"+BrokerFleetStore.NodeId(node.ResourceTargetId),nodeReading,snake)).StatusCode);
            Assert.Equal(HttpStatusCode.BadRequest,(await client.PostAsJsonAsync("/ingest/fleet/observations",batch with {Reports=[batch.Reports[0]]},snake)).StatusCode);
            Assert.Equal(HttpStatusCode.OK,(await client.PostAsJsonAsync("/ingest/fleet/observations",batch,snake)).StatusCode);
            await factory.Services.GetRequiredService<PlatformMonitor>().RefreshAsync();
            Assert.Equal(3,factory.Services.GetRequiredService<PlatformMonitor>().Snapshot().Targets.Count);
            Assert.All(fleet.Observations(),r=>Assert.Equal("HEALTHY",r.Status));
            Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync("/ingest/fleet/observations",batch,snake)).StatusCode);
            client.DefaultRequestHeaders.Authorization=new("Bearer",publisher);
            Assert.Equal(HttpStatusCode.OK,(await client.PostAsJsonAsync("/ingest/fleet/roster",roster with {CheckedAt=DateTimeOffset.UtcNow,Nodes=[]},snake)).StatusCode);
            client.DefaultRequestHeaders.Authorization=new("Bearer",observer);
            Assert.Equal(HttpStatusCode.Conflict,(await client.PostAsJsonAsync("/ingest/fleet/observations",batch,snake)).StatusCode);
            Assert.DoesNotContain(factory.Services.GetRequiredService<PlatformMonitor>().Snapshot().Targets,r=>r.TargetId==BrokerFleetStore.NodeId(node.ResourceTargetId));

            // A complete bounded roster is larger than the old per-target 4 KiB limit
            var many=Enumerable.Range(0,64).Select(_=>new FleetNode(Guid.NewGuid(),Guid.NewGuid(),new string('n',80),new string('s',80),"https://10.10.0.2:6443")).ToArray();
            client.DefaultRequestHeaders.Authorization=new("Bearer",publisher);
            Assert.Equal(HttpStatusCode.OK,(await client.PostAsJsonAsync("/ingest/fleet/roster",roster with {CheckedAt=DateTimeOffset.UtcNow,Nodes=many,Windows=[]},snake)).StatusCode);
            using var oversized=new StringContent(new string('x',262145),Encoding.UTF8,"application/json");
            Assert.Equal(HttpStatusCode.RequestEntityTooLarge,(await client.PostAsync("/ingest/fleet/roster",oversized)).StatusCode);
        }
        finally
        {
            Environment.SetEnvironmentVariable("SENTINEL_AUTH_FILE",authBefore);Environment.SetEnvironmentVariable(pubName,null);Environment.SetEnvironmentVariable(obsName,null);
            Directory.Delete(directory,true);
        }
    }
}
