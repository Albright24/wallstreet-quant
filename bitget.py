"""Bitget public REST market data. No authenticated or order-writing endpoints."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from .core import number, timestamp

BASE = "https://api.bitget.com"
INSTRUMENTS = "/api/v3/market/instruments"
TICKERS = "/api/v3/market/tickers"
STOCK_INFO = "/api/v3/reality/market/stock-info"
STATES = "/api/v3/reality/market/states"
CALENDAR = "/api/v3/reality/market/calendar"
PUBLIC_PATHS = {INSTRUMENTS, TICKERS, STOCK_INFO, STATES, CALENDAR}


class MarketDataError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def symbol_name(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,40}", symbol):
        raise MarketDataError("Use the exact uppercase Bitget symbol")
    return symbol


class BitgetPublic:
    """Bounded GET requests, with a conservative per-client 1 request/sec limit.

    transport injection supports deterministic tests. Never uses API secrets.
    """
    def __init__(self, transport=None, timeout=15, retries=1):
        self.transport = transport
        self.timeout, self.retries = timeout, retries
        self.last_request = 0.0
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path, params=None):
        if path not in PUBLIC_PATHS:
            raise MarketDataError("Endpoint is not an allowed public market-data GET")
        params = params or {}
        query = urllib.parse.urlencode(sorted(params.items()))
        for attempt in range(self.retries + 1):
            try:
                if self.transport:
                    payload = self.transport(path, params)
                else:
                    delay = 1.05 - (time.monotonic() - self.last_request)
                    if delay > 0:
                        time.sleep(delay)
                    self.last_request = time.monotonic()
                    request = urllib.request.Request(BASE + path + ("?" + query if query else ""),
                        headers={"Accept": "application/json", "User-Agent": "WallstreetQuant/0.2"}, method="GET")
                    with self.opener.open(request, timeout=self.timeout) as response:
                        raw = response.read(5_000_001)
                    if len(raw) > 5_000_000:
                        raise MarketDataError("Response exceeds size limit")
                    payload = json.loads(raw)
                if not isinstance(payload, dict) or payload.get("code") != "00000":
                    code = payload.get("code", "unknown") if isinstance(payload, dict) else "invalid"
                    raise MarketDataError(f"Bitget returned API error code {code}")
                if "data" not in payload:
                    raise MarketDataError("Missing Bitget data")
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise MarketDataError(f"Bitget HTTP {exc.code}; no cached quote substituted") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise MarketDataError("Bitget network request failed; no cached quote substituted") from None
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise MarketDataError("Bitget returned invalid JSON") from None
            if not self.transport:
                time.sleep(1 + attempt)

    def instruments(self):
        rows = self.get(INSTRUMENTS, {"category": "SPOT"})["data"]
        if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
            raise MarketDataError("Invalid instrument list")
        return rows

    def instrument(self, symbol):
        symbol_name(symbol)
        rows = self.get(INSTRUMENTS, {"category": "SPOT", "symbol": symbol})["data"]
        if not isinstance(rows, list):
            raise MarketDataError("Invalid instrument response")
        matches = [x for x in rows if isinstance(x, dict) and x.get("symbol") == symbol]
        if len(matches) != 1:
            raise MarketDataError("Instrument not found or ambiguous")
        return matches[0]

    def ticker(self, symbol):
        symbol_name(symbol)
        payload = self.get(TICKERS, {"category": "SPOT", "symbol": symbol})
        rows = payload["data"]
        if not isinstance(rows, list):
            raise MarketDataError("Invalid ticker response")
        matches = [x for x in rows if isinstance(x, dict) and x.get("symbol") == symbol]
        if len(matches) != 1:
            raise MarketDataError("Ticker not found or ambiguous")
        return matches[0]

    def snapshot(self, symbol):
        instrument = self.instrument(symbol)
        ticker = self.ticker(symbol)  # Read prices last; never use discovery time as price time.
        received = datetime.now(timezone.utc)
        result = {"source": "bitget_public_rest", "received_at": received.isoformat(),
                  "instrument": instrument, "ticker": ticker, "quote": None,
                  "paper_quote_usable": False, "execution": "LOCAL_PAPER_ONLY"}
        try:
            quote = normalize_quote(instrument, ticker, received)
            result.update(quote=quote, paper_quote_usable=True)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            result["blocked_reason"] = str(exc)
        return result


def stock_candidate(row):
    # RWA alone does not establish that an asset is a stock.
    return row.get("symbolType") == "stock" or str(row.get("isReality", "")).lower() == "yes"


def normalize_quote(instrument, ticker, received):
    """Require real two-sided size and exchange timestamp; never invent liquidity."""
    symbol = symbol_name(instrument["symbol"])
    if ticker.get("symbol") != symbol:
        raise MarketDataError("Ticker instrument mismatch")
    if instrument.get("category") != "SPOT" or ticker.get("category") != "SPOT":
        raise MarketDataError("Only SPOT instruments are supported")
    if instrument.get("quoteCoin") != "USDT":
        raise MarketDataError("Paper account requires USDT quote currency")
    if instrument.get("status") != "online":
        raise MarketDataError("Instrument is not online")
    ts = number(ticker["ts"])
    observed = datetime.fromtimestamp(float(ts / 1000), timezone.utc)
    age = (received - observed).total_seconds()
    if not 0 <= age <= 15:
        raise MarketDataError("Exchange quote is stale or future-dated; check the local clock")
    bid, ask = number(ticker["bid1Price"]), number(ticker["ask1Price"])
    bid_size, ask_size = number(ticker["bid1Size"]), number(ticker["ask1Size"])
    if bid <= 0 or ask < bid or bid_size <= 0 or ask_size <= 0:
        raise MarketDataError("Ticker has no usable two-sided price and size")
    precision = int(instrument["quantityPrecision"])
    if str(precision) != str(instrument["quantityPrecision"]) or not 0 <= precision <= 12:
        raise MarketDataError("Invalid instrument quantity precision")
    limits = {k: str(number(instrument[k])) for k in ("minOrderQty", "maxOrderQty", "minOrderAmount")}
    if any(number(x) < 0 for x in limits.values()):
        raise MarketDataError("Invalid instrument order limits")
    return {"symbol": symbol, "observed_at": observed.isoformat(), "received_at": received.isoformat(),
            "bid": str(bid), "ask": str(ask), "bid_size": str(bid_size), "ask_size": str(ask_size),
            "source": "bitget_public_ticker", "synthetic": False,
            "liquidity_basis": "reported_best_bid_ask_only", "category": "SPOT",
            "instrument_limits": {**limits, "quantityPrecision": precision},
            "instrument_status": instrument["status"]}


def process_live_paper(engine, client, event, decision):
    """Decision arrives first; fetch current metadata and prices immediately before checks.

    Network failures are audited as blocked paper attempts, never simulated fills.
    Use a new event revision for an intentional retry after a rejected attempt.
    """
    symbol = event.get("symbol")
    if symbol not in engine.risk.symbols:
        raise MarketDataError("Instrument is not in the configured allowlist")
    try:
        snapshot = client.snapshot(symbol)
    except MarketDataError as exc:
        snapshot = {"quote": None, "blocked_reason": str(exc)}
    quote = snapshot["quote"] or {"symbol": symbol, "source": "bitget_public_ticker",
                                   "unavailable_reason": snapshot.get("blocked_reason", "No usable quote")}
    return engine.process(event, decision, quote)
