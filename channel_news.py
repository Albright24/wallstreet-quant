"""Untrusted Telegram post imports; no login, network reader or provenance claim."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from .core import timestamp
from .news import NewsError, SYMBOLS

ALIASES = {"NVDA": r"\b(?:NVDA|Nvidia)\b", "TSLA": r"\b(?:TSLA|Tesla)\b",
           "AAPL": r"\b(?:AAPL|Apple|iPhone|iPad|MacBook)\b", "MSFT": r"\b(?:MSFT|Microsoft|Azure)\b",
           "AMZN": r"\b(?:AMZN|Amazon|AWS|Amazon Web Services|Kindle|Audible|Fire TV)\b"}


def match_companies(body):
    # Links and channel signatures are provenance, not company mentions.
    text = re.sub(r'https?://\S+|\(@\w+\)', ' ', body)
    return {ticker: sorted({m.group(0) for m in re.finditer(pattern, text, re.I)})
            for ticker, pattern in ALIASES.items() if re.search(pattern, text, re.I)}


def normalize_post(post, registry, now=None):
    """Import one explicit post envelope, retaining unmatched macro posts for review.

    Caller-supplied verified/symbol/URL fields never confer trust. Names match
    mentions only, not economic relevance. Edits retain original publication age.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(post, dict):
        raise NewsError("Post must be an object")
    handle = post.get("channel", "")
    if not isinstance(handle, str):
        raise NewsError("Invalid channel")
    handle = handle.removeprefix("@").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", handle):
        raise NewsError("Invalid channel")
    entry = registry.get("channels", {}).get(handle, {})
    if entry.get("enabled") is not True:
        raise NewsError("Channel is unknown or disabled pending review")
    message_id = post.get("message_id")
    if type(message_id) is not int or message_id <= 0:
        raise NewsError("Positive integer message_id required")
    body = post.get("text")
    if not isinstance(body, str) or not body.strip() or len(body) > 12000:
        raise NewsError("Post text must contain 1–12000 characters")
    body = body.strip()
    try:
        published = timestamp(post.get("published_at"))
        updated = timestamp(post.get("edited_at") or post.get("published_at"))
    except (ValueError, TypeError):
        raise NewsError("Post timestamps must be timezone-aware ISO strings") from None
    if updated < published or max(updated, published) > now + timedelta(seconds=30):
        raise NewsError("Post timestamps are inconsistent or in the future")
    url = f"https://t.me/{handle}/{message_id}"
    revision = hashlib.sha256(json.dumps([updated.isoformat(), body], ensure_ascii=False).encode()).hexdigest()
    base = {"source": "telegram:" + handle, "revision": revision,
            "published_at": published.isoformat(), "updated_at": updated.isoformat(),
            "retrieved_at": now.isoformat(), "headline": body.splitlines()[0][:500],
            "body": body, "url": url, "verified": False, "synthetic": False,
            "verification": "local_import_unverified; primary corroboration required",
            "claim_status": "unverified", "publisher": handle,
            "evidence": [{"id": "telegram_post", "url": url, "text": body}]}
    matches = match_companies(body)
    events = [dict(base, id=f"{message_id}:{ticker}", ticker=ticker, symbol=SYMBOLS[ticker],
                   matched_terms=terms, match_method='company_or_product_mention')
              for ticker, terms in matches.items()]
    return {"events": events, "unmatched": [] if events else [dict(base, id=str(message_id))]}


def import_posts(payload, registry, now=None):
    posts = payload if isinstance(payload, list) else [payload]
    if len(posts) > 1000:
        raise NewsError("Import limit is 1000 posts")
    result = {"events": [], "unmatched": [], "connection": "offline_import_only"}
    for post in posts:
        normalized = normalize_post(post, registry, now)
        for key in ("events", "unmatched"):
            result[key].extend(normalized[key])
    return result
