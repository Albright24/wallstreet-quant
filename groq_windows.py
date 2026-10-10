"""Windows Groq transport using the locally verified PowerShell HTTP client."""
import json
import os
import subprocess


SCRIPT = r'''
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$body = [Console]::In.ReadToEnd()
try {
    $response = Invoke-RestMethod `
        -Uri "https://api.groq.com/openai/v1/chat/completions" `
        -Headers @{ Authorization = "Bearer $env:GROQ_API_KEY" } `
        -ContentType "application/json" `
        -Method Post `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
        -MaximumRedirection 0 `
        -TimeoutSec 120
    $json = $response | ConvertTo-Json -Depth 40 -Compress
    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 1000000) {
        [Console]::Out.Write('{"transport_error":0}')
        exit 1
    }
    [Console]::Out.Write($json)
} catch {
    $status = 0
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    [Console]::Out.Write('{"transport_error":' + $status + '}')
    exit 1
}
'''


def request(payload, key):
    # Neither key nor untrusted news is interpolated into shell code or arguments.
    # Do not pass unrelated exchange/Telegram credentials to the child process.
    allowed = {"systemroot", "windir", "temp", "tmp", "userprofile", "localappdata", "path", "psmodulepath"}
    env = {k: v for k, v in os.environ.items() if k.lower() in allowed}
    env["GROQ_API_KEY"] = key
    root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            input=json.dumps(payload, allow_nan=False).encode("utf-8"),
            capture_output=True, env=env, timeout=135, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Groq Windows connection failed or timed out; not retried automatically") from None
    if len(result.stdout) > 1_000_000:
        raise ValueError("Model response exceeds size cap")
    try:
        response = json.loads(result.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise ValueError("Invalid Groq Windows response; not retried automatically") from None
    if result.returncode or (isinstance(response, dict) and "transport_error" in response):
        code = response.get("transport_error") if isinstance(response, dict) else None
        if type(code) is int and 100 <= code <= 599:
            raise ValueError(f"Model request failed (HTTP {code}); not retried automatically")
        raise ValueError("Groq Windows request failed; not retried automatically")
    return response
