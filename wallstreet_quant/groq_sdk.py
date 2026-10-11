"""Official Groq client transport; no retries, redirects, or credential logging."""
import json


def request(payload, key):
    try:
        import httpx
        from groq import Groq, APIStatusError
    except ImportError:
        raise ValueError('Install the groq package in this Python environment') from None
    try:
        with Groq(api_key=key, max_retries=0, timeout=120,
                  http_client=httpx.Client(follow_redirects=False, timeout=120)) as client:
            response = client.chat.completions.create(**payload).model_dump()
        if len(json.dumps(response, allow_nan=False).encode()) > 1_000_000:
            raise ValueError('Model response exceeds size cap')
        return response
    except APIStatusError as error:
        raise ValueError(f'Model request failed (HTTP {error.status_code}); not retried automatically') from None
    except Exception:
        raise ValueError('Groq SDK request failed; not retried automatically') from None
