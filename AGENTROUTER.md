# AgentRouter connection (v0.6.1)

This is an optional third-party Responses API adapter, not a verified direct
OpenAI connection. AgentRouter receives the submitted event and account context.
The model name is the provider's requested routing label; this code cannot
independently verify the upstream model identity.

In the existing Windows project PowerShell session:

```powershell
$env:WQ_AI_PROVIDER = "agentrouter"
$env:WQ_MODEL = "gpt-6-astra"
$agentRouterSecret = Read-Host "AgentRouter API key" -AsSecureString
$env:AGENTROUTER_API_KEY = [System.Net.NetworkCredential]::new('', $agentRouterSecret).Password
.\.venv\Scripts\python.exe -m wallstreet_quant.ai_check
```

Run the key-entry lines separately. Enter the key only at the hidden prompt.
If the key is already set in this PowerShell session, skip the two key-entry lines.
Never put keys in source files, screenshots, or uploaded archives.

The check makes ONE billable request to https://co.agentrouter.org/v1/responses,
using an explicitly synthetic event with no evidence. It checks the required
structured decision contract and abstention. It does not call a broker, use news
data, open a database, or place any trade. It waits up to 120 seconds and never
automatically retries. A failure leaves the existing paper ledger untouched.

The adapter preserves strict response validation, input/output limits, and
redirect refusal. OpenAI mode still reads only OPENAI_API_KEY; AgentRouter mode
reads only AGENTROUTER_API_KEY. Unset WQ_AI_PROVIDER or set it to openai to use the
original direct OpenAI route. Merely setting a key does not connect the agent.

An HTTP 401/403 can mean a key, account, or permission problem; 404 can mean an
unsupported route; 429 can mean a rate or quota limit. Do not repeatedly retry.
Share only the fixed diagnostic output, never the key or raw HTTP response.

Live provider compatibility has not yet been confirmed. Passing the connection
check does not establish news accuracy, trading performance, or full integration
of the Telegram collector with the trading worker.
