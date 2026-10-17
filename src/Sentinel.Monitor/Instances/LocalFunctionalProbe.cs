using System.Net.Http.Headers;
using System.Text.Json;

namespace Sentinel.Monitor.Instances;

public sealed record FunctionalObservation(string CheckId, string Scope, string Status,
    string ReasonCode, DateTimeOffset CheckedAt);

internal static class LocalFunctionalProbe
{
    internal static async Task<FunctionalObservation> Check(HttpClient http, LocalFunctionalCheck check,
        int timeoutSeconds, TimeProvider clock, CancellationToken token)
    {
        FunctionalObservation Result(string status, string reason) =>
            new(check.CheckId, "local_functional_fixture", status, reason, clock.GetUtcNow());
        var nonce = Guid.NewGuid().ToString("N");
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(timeoutSeconds));
        using var request = new HttpRequestMessage(HttpMethod.Get, check.Url + "?left=17&right=25&nonce=" + nonce);
        // Control-plane credentials and cookies must never reach a workload
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        try
        {
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
            var code = (int)response.StatusCode;
            if (code >= 500) return Result("DOWN", "FUNCTION_UNAVAILABLE");
            if (code != 200) return Result("CHECKER_ERROR", "UNEXPECTED_HTTP_STATUS");
            if (response.Content.Headers.ContentType?.MediaType != "application/json")
                return Result("CHECKER_ERROR", "EXPECTED_JSON");
            const int limit = 8192;
            if (response.Content.Headers.ContentLength > limit) return Result("CHECKER_ERROR", "RESPONSE_TOO_LARGE");
            await using var input = await response.Content.ReadAsStreamAsync(deadline.Token);
            using var body = new MemoryStream();
            var buffer = new byte[1024];
            int count;
            while ((count = await input.ReadAsync(buffer, deadline.Token)) > 0)
            {
                if (body.Length + count > limit) return Result("CHECKER_ERROR", "RESPONSE_TOO_LARGE");
                body.Write(buffer, 0, count);
            }
            using var json = JsonDocument.Parse(body.ToArray());
            var root = json.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("service", out var service) || service.ValueKind != JsonValueKind.String
                || service.GetString() != "sentinel-functional-fixture-v1"
                || !root.TryGetProperty("nonce", out var echoed) || echoed.ValueKind != JsonValueKind.String
                || echoed.GetString() != nonce
                || !root.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Number
                || !result.TryGetInt32(out var answer))
                return Result("CHECKER_ERROR", "INVALID_FUNCTION_RESPONSE");
            return answer == 42 ? Result("UP", "FUNCTION_RESULT_CONFIRMED") : Result("DOWN", "WRONG_FUNCTION_RESULT");
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested) { return Result("DOWN", "FUNCTION_TIMEOUT"); }
        catch (HttpRequestException) { return Result("DOWN", "FUNCTION_UNREACHABLE"); }
        catch (IOException) { return Result("DOWN", "FUNCTION_UNREACHABLE"); }
        catch (JsonException) { return Result("CHECKER_ERROR", "INVALID_JSON"); }
    }
}
