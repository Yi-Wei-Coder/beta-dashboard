"""Fetch daily close prices for crypto tokens from free, no-key sources.

Primary source : Binance spot klines (USDT pairs), paginated.
Fallback source: Gate.io spot candlesticks (for tokens not on Binance, e.g. SQD).

Public functions
----------------
get_price_matrix(start="2022-01-01") -> pandas.DataFrame
    Wide frame indexed by date (daily), one column per token symbol, values = USD close.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import time

import pandas as pd
import requests

# Display symbol -> exchange ticker (base asset). "Virtuals" trades as VIRTUAL.
TOKENS: dict[str, str] = {
    "BTC": "BTC",
    "ETH": "ETH",
    "SOL": "SOL",
    "LDO": "LDO",
    "ENA": "ENA",
    "SQD": "SQD",
    "PENDLE": "PENDLE",
    "AAVE": "AAVE",
    "TON": "TON",
    "PUMP": "PUMP",
    "VIRTUAL": "VIRTUAL",
    "SKY": "SKY",
}

_BINANCE = "https://api.binance.com/api/v3/klines"
_GATE = "https://api.gateio.ws/api/v4/spot/candlesticks"
_DAY = 86_400  # seconds


def _to_ms(d: str | dt.date) -> int:
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    return int(dt.datetime(d.year, d.month, d.day).timestamp() * 1000)


def fetch_binance(base: str, start_ms: int) -> pd.Series | None:
    """Daily close series from Binance, paginating 1000 candles at a time."""
    rows: list[list] = []
    cursor = start_ms
    while True:
        resp = requests.get(
            _BINANCE,
            params={"symbol": f"{base}USDT", "interval": "1d",
                    "startTime": cursor, "limit": 1000},
            timeout=30,
        )
        if resp.status_code != 200:
            return None  # symbol not on Binance (or transient error)
        data = resp.json()
        if not data:
            break
        rows.extend(data)
        if len(data) < 1000:
            break
        cursor = data[-1][0] + _DAY * 1000
        time.sleep(0.2)
    if not rows:
        return None
    idx = [dt.datetime.utcfromtimestamp(r[0] / 1000).date() for r in rows]
    vals = [float(r[4]) for r in rows]  # close
    s = pd.Series(vals, index=pd.to_datetime(idx))
    return s[~s.index.duplicated(keep="last")]


def fetch_gate(base: str, start_s: int, end_s: int) -> pd.Series | None:
    """Daily close series from Gate.io, paginating <=1000 days at a time."""
    rows: list[list] = []
    cursor = start_s
    while cursor < end_s:
        to = min(cursor + _DAY * 999, end_s)
        resp = requests.get(
            _GATE,
            params={"currency_pair": f"{base}_USDT", "interval": "1d",
                    "from": cursor, "to": to},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, list) or not data:
            break
        rows.extend(data)
        cursor = int(data[-1][0]) + _DAY
        time.sleep(0.2)
    if not rows:
        return None
    # Gate kline: [ts, quote_vol, close, high, low, open, base_vol, closed]
    idx = [dt.datetime.utcfromtimestamp(int(r[0])).date() for r in rows]
    vals = [float(r[2]) for r in rows]  # close
    s = pd.Series(vals, index=pd.to_datetime(idx))
    return s[~s.index.duplicated(keep="last")]


def fetch_token(symbol: str, start: str) -> pd.Series | None:
    base = TOKENS[symbol]
    s = fetch_binance(base, _to_ms(start))
    if s is not None and not s.empty:
        return s
    start_s = int(dt.datetime.fromisoformat(start).timestamp())
    return fetch_gate(base, start_s, int(time.time()))


def get_price_matrix(start: str = "2022-01-01") -> pd.DataFrame:
    """Return a wide daily close-price frame (index=date, columns=symbols)."""
    series: dict[str, pd.Series] = {}
    for sym in TOKENS:
        s = fetch_token(sym, start)
        if s is not None and not s.empty:
            series[sym] = s
    if not series:
        return pd.DataFrame()
    df = pd.DataFrame(series).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


DATA_CSV = pathlib.Path(__file__).parent / "data" / "prices.csv"


def update_price_matrix(csv_path: pathlib.Path = DATA_CSV,
                        start: str = "2022-01-01") -> pd.DataFrame:
    """Incrementally update the stored price CSV.

    Reads the existing CSV (if any) and only fetches days *after* each token's
    last stored date (re-pulling a 3-day overlap to repair the partial latest
    candle). New values take precedence on overlapping dates. Avoids
    re-scraping the full history on every run.
    """
    existing: pd.DataFrame | None = None
    if csv_path.exists():
        existing = pd.read_csv(csv_path, index_col=0, parse_dates=True).sort_index()

    series: dict[str, pd.Series] = {}
    for sym in TOKENS:
        tok_start = start
        if existing is not None and sym in existing and existing[sym].notna().any():
            last = existing[sym].last_valid_index()
            tok_start = (last - pd.Timedelta(days=3)).date().isoformat()
        s = fetch_token(sym, tok_start)
        if s is not None and not s.empty:
            series[sym] = s

    fetched = pd.DataFrame(series).sort_index() if series else pd.DataFrame()
    if existing is not None and not existing.empty:
        combined = fetched.combine_first(existing) if not fetched.empty else existing
    else:
        combined = fetched
    combined = combined.sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]

    csv_path.parent.mkdir(exist_ok=True)
    combined.to_csv(csv_path)
    return combined


if __name__ == "__main__":
    frame = update_price_matrix()
    print(f"Saved {frame.shape[0]} rows x {frame.shape[1]} cols -> {DATA_CSV}")
    cov = frame.apply(lambda c: f"{c.first_valid_index().date()} -> {c.last_valid_index().date()}")
    print(cov.to_string())
