"""News adapters. Source provenance is distinct from factual corroboration."""
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
import re
import time
import urllib.parse
import urllib.request
from .bitget import NoRedirect
from .core import timestamp

SYMBOLS = {"NVDA": "RNVDAUSDT", "TSLA": "RTSLAUSDT", "AAPL": "RAAPLUSDT",
           "MSFT": "RMSFTUSDT", "AMZN": "RAMZNUSDT"}


class NewsError(ValueError):
    pass


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_text(value, limit=12000):
    parser = TextOnly()
    parser.feed(str(value))
    return " ".join(" ".join(parser.parts).split())[:limit]


class NewsHTTP:
    def __init__(self):
        self.last = 0.0
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, url, headers, as_json=True):
        host = urllib.parse.urlsplit(url).hostname
        if host not in ("data.sec.gov", "www.sec.gov", "data.alpaca.markets") or not url.startswith("https://"):
            raise NewsError("News adapter destination is not allowed")
        delay = 1.05 - (time.monotonic() - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise NewsError("News response exceeds 5 MB limit")
            return json.loads(raw) if as_json else raw.decode("utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            if isinstance(exc, NewsError):
                raise
            # Avoid echoing URLs, headers, article contents or credentials on failure.
            raise NewsError("News request failed; no stale or synthetic substitution") from None


def alpaca_events(article):
    """Alpaca authenticates delivery, not the truth of the article's claims."""
    published = timestamp(article["created_at"]).isoformat()
    updated = timestamp(article["updated_at"]).isoformat()
    if timestamp(updated) < timestamp(published):
        raise NewsError("Article update predates publication")
    url = str(article["url"])
    if urllib.parse.urlsplit(url).scheme != "https":
        raise NewsError("Article evidence URL must use HTTPS")
    body = clean_text(article.get("content") or article.get("summary", ""))
    headline = clean_text(article["headline"], 500)
    revision = hashlib.sha256(json.dumps([updated, headline, body, url]).encode()).hexdigest()
    result = []
    for ticker in sorted(set(article.get("symbols", [])) & SYMBOLS.keys()):
        result.append({"source": "alpaca_news", "id": str(article["id"]) + ":" + ticker,
            "revision": revision, "symbol": SYMBOLS[ticker], "ticker": ticker,
            "published_at": published, "updated_at": updated, "headline": headline,
            "body": body, "url": url, "verified": False, "synthetic": False,
            "verification": "provider_delivery_only; primary corroboration required",
            "publisher": str(article.get("source", "unknown")),
            "evidence": [{"id": "article", "url": url, "text": body}]})
    return result


class AlpacaNews:
    def __init__(self, http=None):
        self.http = http or NewsHTTP()

    def fetch(self, start, page_token=None):
        timestamp(start)
        key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise NewsError("Configure APCA_API_KEY_ID and APCA_API_SECRET_KEY locally")
        params = {"start": start, "symbols": ",".join(SYMBOLS), "limit": 50,
                  "sort": "asc", "include_content": "true"}
        if page_token:
            params["page_token"] = page_token
        payload = self.http.get("https://data.alpaca.markets/v1beta1/news?" + urllib.parse.urlencode(params),
             {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        events = [e for a in payload["news"] for e in alpaca_events(a)]
        return {"events": events, "next_page_token": payload.get("next_page_token"),
                "realtime_entitlement": "not_verified"}


class SECNews:
    """Resolve CIK from SEC metadata; read recent primary 8-K/10-Q/10-K documents.

    Fetches primary documents only, not linked exhibits. Never promotes secondary
    claims to verified based on ticker/time coincidence.
    """
    def __init__(self, http=None):
        self.http = http or NewsHTTP()

    def fetch(self, ticker, limit=3):
        if ticker not in SYMBOLS or not 1 <= limit <= 5:
            raise NewsError("Use a configured ticker and a limit from 1 to 5")
        agent = os.getenv("SEC_USER_AGENT", "")
        if not re.search(r"\S+@\S+\.\S+", agent):
            raise NewsError("Set SEC_USER_AGENT to your application name and real contact email")
        headers = {"User-Agent": agent, "Accept": "application/json,text/html"}
        mapping = self.http.get("https://www.sec.gov/files/company_tickers_exchange.json", headers)
        rows = [dict(zip(mapping["fields"], r)) for r in mapping["data"]]
        matches = [r for r in rows if r.get("ticker") == ticker]
        if len(matches) != 1:
            raise NewsError("SEC ticker mapping is missing or ambiguous")
        cik = int(matches[0]["cik"])
        data = self.http.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers)
        recent = data["filings"]["recent"]
        events = []
        for i, form in enumerate(recent["form"]):
            if form not in ("8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"):
                continue
            accession = recent["accessionNumber"][i]
            document = recent["primaryDocument"][i]
            if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(r"[A-Za-z0-9_.-]+", document):
                raise NewsError("Unexpected SEC document identity")
            accepted = timestamp(recent["acceptanceDateTime"][i]).isoformat()
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"
            raw = self.http.get(url, headers, as_json=False)
            body = clean_text(raw)
            if len(body) < 100:
                raise NewsError("SEC primary document has insufficient readable text")
            events.append({"source": "sec_edgar", "id": accession + ":" + ticker,
                "revision": hashlib.sha256(raw.encode()).hexdigest(), "symbol": SYMBOLS[ticker],
                "ticker": ticker, "published_at": accepted, "headline": f"{ticker} filed {form}",
                "body": body, "url": url, "verified": True, "synthetic": False,
                "verification": "primary_document_fetched_from_SEC; claims_not_independently_audited",
                "form": form, "cik": str(cik), "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "content_scope": "primary_document_first_12000_characters; exhibits_not_fetched",
                "evidence": [{"id": "filing", "url": url, "text": body}]})
            if len(events) == limit:
                break
        return events
