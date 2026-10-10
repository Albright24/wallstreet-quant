# Groq setup (v0.6.4)

Use a Groq Free tier account. Create a key at https://console.groq.com/.
The key is entered locally, never sent to this chat or stored in the project.
In PowerShell, from the project folder, run this first and then paste your key
at the hidden prompt and press Enter:

```powershell
$groqSecret = Read-Host "Paste your Groq API key" -AsSecureString
```

After the normal PowerShell prompt returns, run:

```powershell
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', $groqSecret).Password.Trim()
$groqSecret.Dispose()
Remove-Variable groqSecret
$env:WQ_AI_PROVIDER = "groq"
$env:WQ_MODEL = "qwen/qwen3.8-27b"
.\.venv\Scripts\python.exe -m wallstreet_quant.ai_check
```

This makes one synthetic API call without a broker, database or trades.
Expected result: status passed, action skip or hold, trades_placed 0.
Environment settings last only for this PowerShell session.
The production pipeline retains its daily request cap and evidence/risk gates.
No automatic provider fallback or request retries. HTTP 429 means quota/rate
limit reached; do not loop retries. No live-account test has been run by the
developer without the user's local key.

Groq endpoint: https://api.groq.com/openai/v1/chat/completions
Strict JSON schema documentation: https://console.groq.com/docs/structured-outputs

Windows Groq calls now automatically use built-in Windows PowerShell. No groq pip package is needed. Linux continues to use urllib; VPS connectivity has not been verified. Keys remain session-local.
