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

# Universe for the "Altcoin Marketcap (excl BTC & ETH)" benchmark index.
# Binance base ticker -> CoinGecko id (id is used to pull current market cap
# from DeFiLlama, which gives us circulating supply = mcap / current price).
# Non-stablecoin large caps; coins that fail to fetch are skipped gracefully.
ALT_UNIVERSE: dict[str, str] = {
    "SOL": "solana", "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
    "DOGE": "dogecoin", "TRX": "tron", "AVAX": "avalanche-2", "LINK": "chainlink",
    "DOT": "polkadot", "LTC": "litecoin", "BCH": "bitcoin-cash", "UNI": "uniswap",
    "ATOM": "cosmos", "XLM": "stellar", "ETC": "ethereum-classic", "NEAR": "near",
    "FIL": "filecoin", "APT": "aptos", "ICP": "internet-computer",
    "HBAR": "hedera-hashgraph", "SUI": "sui", "ARB": "arbitrum", "OP": "optimism",
    "INJ": "injective-protocol", "SHIB": "shiba-inu", "TON": "the-open-network",
    "PEPE": "pepe", "AAVE": "aave", "MKR": "maker", "VET": "vechain",
    "RENDER": "render-token", "ALGO": "algorand",
}

_BINANCE = "https://api.binance.com/api/v3/klines"
_MCAPS = "https://coins.llama.fi/mcaps"
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


def fetch_one(base: str, start: str, gate_fallback: bool = True) -> pd.Series | None:
    """Daily close series for one base ticker: Binance first, Gate.io fallback."""
    s = fetch_binance(base, _to_ms(start))
    if s is not None and not s.empty:
        return s
    if not gate_fallback:
        return None
    start_s = int(dt.datetime.fromisoformat(start).timestamp())
    return fetch_gate(base, start_s, int(time.time()))


def fetch_token(symbol: str, start: str) -> pd.Series | None:
    return fetch_one(TOKENS[symbol], start)


DATA_DIR = pathlib.Path(__file__).parent / "data"
DATA_CSV = DATA_DIR / "prices.csv"
ALT_CSV = DATA_DIR / "altmkt_prices.csv"
MKT_CSV = DATA_DIR / "marketcap_index.csv"
MKT_COL = "MKT_EXBTCETH"


def _incremental_update(symbol_base: dict[str, str], csv_path: pathlib.Path,
                        start: str = "2022-01-01",
                        gate_fallback: bool = True) -> pd.DataFrame:
    """Generic incremental price-matrix updater shared by all caches.

    Only fetches days after each symbol's last stored date (re-pulling a 3-day
    overlap to repair the partial latest candle); never re-scrapes from scratch.
    """
    existing: pd.DataFrame | None = None
    if csv_path.exists():
        existing = pd.read_csv(csv_path, index_col=0, parse_dates=True).sort_index()

    series: dict[str, pd.Series] = {}
    for sym, base in symbol_base.items():
        tok_start = start
        if existing is not None and sym in existing and existing[sym].notna().any():
            last = existing[sym].last_valid_index()
            tok_start = (last - pd.Timedelta(days=3)).date().isoformat()
        s = fetch_one(base, tok_start, gate_fallback=gate_fallback)
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


def update_price_matrix(csv_path: pathlib.Path = DATA_CSV,
                        start: str = "2022-01-01") -> pd.DataFrame:
    """Incrementally update the stored 12-token price CSV."""
    return _incremental_update({s: b for s, b in TOKENS.items()}, csv_path, start)


def update_alt_prices(csv_path: pathlib.Path = ALT_CSV,
                      start: str = "2022-01-01") -> pd.DataFrame:
    """Incrementally update the altcoin-universe price CSV (Binance only)."""
    return _incremental_update({b: b for b in ALT_UNIVERSE}, csv_path,
                               start, gate_fallback=False)


def _current_mcaps(ids: list[str]) -> dict[str, float]:
    coins = [f"coingecko:{i}" for i in ids]
    resp = requests.post(_MCAPS, json={"coins": coins}, timeout=30)
    resp.raise_for_status()
    out: dict[str, float] = {}
    for key, val in resp.json().items():
        cid = key.split(":", 1)[1]
        if val and val.get("mcap"):
            out[cid] = float(val["mcap"])
    return out


def build_marketcap_index(alt_csv: pathlib.Path = ALT_CSV,
                          out_csv: pathlib.Path = MKT_CSV) -> pd.Series:
    """Cap-weighted altcoin market index (excl BTC & ETH), rebased to 100.

    supply_i  = current_mcap_i / current_price_i          (from DeFiLlama)
    mcap_i(t) = price_i(t) * supply_i
    index returns are mcap-weighted average of constituent daily returns
    (weights = prior-day mcap, renormalised over coins live that day), then
    chained from a base of 100 so new listings don't create level jumps.
    """
    px = pd.read_csv(alt_csv, index_col=0, parse_dates=True).sort_index()
    mcaps = _current_mcaps(list(ALT_UNIVERSE.values()))

    supply: dict[str, float] = {}
    for base, cid in ALT_UNIVERSE.items():
        if base not in px.columns or cid not in mcaps:
            continue
        last_px = px[base].dropna()
        if last_px.empty or last_px.iloc[-1] <= 0:
            continue
        supply[base] = mcaps[cid] / last_px.iloc[-1]

    cols = list(supply)
    px = px[cols]
    supply_s = pd.Series(supply)

    mcap = px.mul(supply_s, axis=1)        # daily $ mcap per coin
    weights = mcap.shift(1)                # prior-day weights
    rets = px.pct_change()
    valid = weights.notna() & rets.notna()
    weights = weights.where(valid)
    rets = rets.where(valid)
    wsum = weights.sum(axis=1)
    bench_ret = (weights * rets).sum(axis=1) / wsum.replace(0, pd.NA)
    index = 100.0 * (1.0 + bench_ret.fillna(0.0)).cumprod()
    index.name = MKT_COL

    out_csv.parent.mkdir(exist_ok=True)
    index.to_frame().to_csv(out_csv)
    return index


if __name__ == "__main__":
    frame = update_price_matrix()
    print(f"Saved {frame.shape[0]} rows x {frame.shape[1]} cols -> {DATA_CSV}")
    cov = frame.apply(lambda c: f"{c.first_valid_index().date()} -> {c.last_valid_index().date()}")
    print(cov.to_string())

    alts = update_alt_prices()
    print(f"\nAlt universe: {alts.shape[1]} coins, {alts.shape[0]} rows -> {ALT_CSV}")
    idx = build_marketcap_index()
    print(f"Marketcap index (excl BTC/ETH): {idx.first_valid_index().date()} "
          f"-> {idx.last_valid_index().date()}, last={idx.iloc[-1]:.1f} -> {MKT_CSV}")
