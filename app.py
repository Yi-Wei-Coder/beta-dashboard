"""Crypto Beta Dashboard — rolling beta of altcoins vs BTC.

Run:  streamlit run app.py
"""
from __future__ import annotations

import pathlib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import fetch_data

DATA_CSV = pathlib.Path(__file__).parent / "data" / "prices.csv"
BENCHMARK = "BTC"

st.set_page_config(page_title="Crypto Beta vs BTC", layout="wide")


@st.cache_data(show_spinner=False)
def load_prices() -> pd.DataFrame:
    if DATA_CSV.exists():
        df = pd.read_csv(DATA_CSV, index_col=0, parse_dates=True)
    else:
        df = fetch_data.get_price_matrix()
        DATA_CSV.parent.mkdir(exist_ok=True)
        df.to_csv(DATA_CSV)
    return df.sort_index()


def refresh_prices() -> None:
    fetch_data.update_price_matrix(DATA_CSV)
    load_prices.clear()


def rolling_beta(prices: pd.DataFrame, token: str, window: int) -> pd.Series:
    """beta = Cov(r_token, r_btc) / Var(r_btc) over a rolling window of daily returns."""
    rets = prices[[token, BENCHMARK]].pct_change()
    cov = rets[token].rolling(window).cov(rets[BENCHMARK])
    var = rets[BENCHMARK].rolling(window).var()
    return cov / var


# ── Sidebar ────────────────────────────────────────────────────────────────
st.sidebar.title("⚙️ Controls")

prices = load_prices()
candidates = [c for c in prices.columns if c != BENCHMARK]

selected = st.sidebar.multiselect(
    "Tokens to compare vs BTC",
    options=candidates,
    default=[t for t in ("SOL", "ETH", "ENA", "PENDLE") if t in candidates],
)

window = st.sidebar.slider(
    "Rolling window (days)", min_value=7, max_value=120, value=30, step=1,
    help="1 month ≈ 30 days. Beta is estimated over this many daily returns.",
)

dmin, dmax = prices.index.min().date(), prices.index.max().date()
date_range = st.sidebar.date_input(
    "Date range", value=(dmin, dmax), min_value=dmin, max_value=dmax,
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d, end_d = dmin, dmax

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh data from exchanges"):
    with st.spinner("Fetching latest prices from Binance / Gate.io…"):
        refresh_prices()
    st.rerun()
st.sidebar.caption("Source: Binance spot (USDT pairs); Gate.io for SQD.")

# ── Main ───────────────────────────────────────────────────────────────────
st.title("📈 Crypto Beta Dashboard — vs BTC")
st.caption(
    f"Rolling **{window}-day** beta of daily returns against {BENCHMARK}. "
    "β = Cov(token, BTC) / Var(BTC).  β>1 = more volatile than BTC, β<1 = less."
)

if not selected:
    st.info("Pick one or more tokens in the sidebar to plot their beta.")
    st.stop()

mask = (prices.index.date >= start_d) & (prices.index.date <= end_d)
window_prices = prices.loc[mask]

fig = go.Figure()
latest = {}
for tok in selected:
    beta = rolling_beta(prices, tok, window).loc[mask].dropna()
    if beta.empty:
        continue
    fig.add_trace(go.Scatter(x=beta.index, y=beta.values, mode="lines", name=tok))
    latest[tok] = beta.iloc[-1]

fig.add_hline(y=1.0, line_dash="dash", line_color="gray",
              annotation_text="β = 1 (moves with BTC)")
fig.add_hline(y=0.0, line_dash="dot", line_color="lightgray")
fig.update_layout(
    height=560, hovermode="x unified",
    yaxis_title=f"{window}-day rolling β", xaxis_title="Date",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(l=10, r=10, t=40, b=10),
)
st.plotly_chart(fig, use_container_width=True)

# Latest beta metrics
if latest:
    st.subheader("Latest β")
    cols = st.columns(len(latest))
    for col, (tok, val) in zip(cols, latest.items()):
        col.metric(tok, f"{val:.2f}")

# Coverage + methodology
with st.expander("ℹ️ Data coverage & methodology"):
    cov = pd.DataFrame({
        "first": [prices[c].first_valid_index().date() for c in prices.columns],
        "last": [prices[c].last_valid_index().date() for c in prices.columns],
        "days": [int(prices[c].notna().sum()) for c in prices.columns],
    }, index=prices.columns)
    st.dataframe(cov, use_container_width=True)
    st.markdown(
        "- **Returns**: daily simple returns from close prices.\n"
        f"- **Beta**: rolling Cov(token, {BENCHMARK}) ÷ Var({BENCHMARK}) over the chosen window.\n"
        "- Newer tokens start at their **exchange-listing date**, not 2022.\n"
        "- Beta appears only after a full window of data is available."
    )
