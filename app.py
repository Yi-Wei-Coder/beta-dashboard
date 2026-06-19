"""Crypto Beta Dashboard — rolling beta of altcoins vs a chosen benchmark.

Run:  streamlit run app.py
"""
from __future__ import annotations

import pathlib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import fetch_data

DATA_CSV = fetch_data.DATA_CSV
MKT_CSV = fetch_data.MKT_CSV
MKT_COL = fetch_data.MKT_COL
MKT_LABEL = "Marketcap (excl BTC, ETH)"

st.set_page_config(page_title="Crypto Beta", layout="wide")


@st.cache_data(show_spinner=False)
def load_data() -> pd.DataFrame:
    """Token prices joined with the altcoin-marketcap benchmark index."""
    if not DATA_CSV.exists():
        fetch_data.update_price_matrix()
    prices = pd.read_csv(DATA_CSV, index_col=0, parse_dates=True).sort_index()
    if not MKT_CSV.exists():
        fetch_data.update_alt_prices()
        fetch_data.build_marketcap_index()
    mkt = pd.read_csv(MKT_CSV, index_col=0, parse_dates=True).sort_index()
    return prices.join(mkt[[MKT_COL]])


def refresh_data() -> None:
    fetch_data.update_price_matrix()
    fetch_data.update_alt_prices()
    fetch_data.build_marketcap_index()
    load_data.clear()


def rolling_beta(returns: pd.DataFrame, token: str, benchmark: str,
                 window: int) -> pd.Series:
    """beta = Cov(r_token, r_bench) / Var(r_bench) over a rolling window."""
    cov = returns[token].rolling(window).cov(returns[benchmark])
    var = returns[benchmark].rolling(window).var()
    return cov / var


def render_beta_tab(returns: pd.DataFrame, selected: list[str], window: int,
                    mask: pd.Series, benchmark: str, bench_label: str) -> None:
    """Shared layout: beta line chart + latest-beta metrics for one benchmark."""
    if not selected:
        st.info("Pick one or more tokens in the sidebar to plot their beta.")
        return

    fig = go.Figure()
    latest: dict[str, float] = {}
    for tok in selected:
        if tok == benchmark:
            continue
        beta = rolling_beta(returns, tok, benchmark, window).loc[mask].dropna()
        if beta.empty:
            continue
        fig.add_trace(go.Scatter(x=beta.index, y=beta.values, mode="lines", name=tok))
        latest[tok] = beta.iloc[-1]

    fig.add_hline(y=1.0, line_dash="dash", line_color="gray",
                  annotation_text=f"β = 1 (moves with {bench_label})")
    fig.add_hline(y=0.0, line_dash="dot", line_color="lightgray")
    fig.update_layout(
        height=560, hovermode="x unified",
        yaxis_title=f"{window}-day rolling β", xaxis_title="Date",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)

    if latest:
        st.subheader("Latest β")
        cols = st.columns(len(latest))
        for col, (tok, val) in zip(cols, latest.items()):
            col.metric(tok, f"{val:.2f}")


# ── Sidebar (shared controls) ───────────────────────────────────────────────
st.sidebar.title("⚙️ Controls")

data = load_data()
returns = data.pct_change()
candidates = [c for c in data.columns if c not in ("BTC", "ETH", MKT_COL)]

selected = st.sidebar.multiselect(
    "Tokens to plot",
    options=candidates,
    default=[t for t in ("SOL", "ENA", "PENDLE", "AAVE") if t in candidates],
)
window = st.sidebar.slider(
    "Rolling window (days)", min_value=7, max_value=120, value=30, step=1,
    help="1 month ≈ 30 days. Beta is estimated over this many daily returns.",
)
dmin, dmax = data.index.min().date(), data.index.max().date()
date_range = st.sidebar.date_input("Date range", value=(dmin, dmax),
                                   min_value=dmin, max_value=dmax)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d, end_d = dmin, dmax
mask = (data.index.date >= start_d) & (data.index.date <= end_d)

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh data from exchanges"):
    with st.spinner("Fetching latest prices & rebuilding marketcap index…"):
        refresh_data()
    st.rerun()
st.sidebar.caption("Prices: Binance / Gate.io. Marketcap weights: DeFiLlama.")

# ── Main ────────────────────────────────────────────────────────────────────
st.title("📈 Crypto Beta Dashboard")
st.caption(f"Rolling **{window}-day** beta of daily returns.  "
           "β = Cov(token, benchmark) / Var(benchmark).")

tab_btc, tab_mkt = st.tabs(["Beta vs BTC", "Beta vs Marketcap (excl BTC, ETH)"])

with tab_btc:
    render_beta_tab(returns, selected, window, mask, "BTC", "BTC")

with tab_mkt:
    choice = st.radio(
        "Compare against", [MKT_LABEL, "BTC"], horizontal=True,
        help="Switch the benchmark used for the beta calculation in this tab.",
    )
    bench = MKT_COL if choice == MKT_LABEL else "BTC"
    render_beta_tab(returns, selected, window, mask, bench, choice)
    if bench == MKT_COL:
        st.caption("Benchmark = cap-weighted index of 30+ large alts (SOL, BNB, XRP, "
                   "ADA, DOGE, TRX, AVAX, LINK, …), excl. BTC, ETH & stablecoins.")

# ── Coverage + methodology ───────────────────────────────────────────────────
with st.expander("ℹ️ Data coverage & methodology"):
    price_cols = [c for c in data.columns if c != MKT_COL]
    cov = pd.DataFrame({
        "first": [data[c].first_valid_index().date() for c in price_cols],
        "last": [data[c].last_valid_index().date() for c in price_cols],
        "days": [int(data[c].notna().sum()) for c in price_cols],
    }, index=price_cols)
    st.dataframe(cov, use_container_width=True)
    st.markdown(
        "- **Returns**: daily simple returns from close prices.\n"
        "- **Beta**: rolling Cov(token, benchmark) ÷ Var(benchmark) over the window.\n"
        f"- **{MKT_LABEL}**: a cap-weighted altcoin index. Per-coin circulating "
        "supply = current marketcap (DeFiLlama) ÷ current price; historical marketcap "
        "= price × supply; index = mcap-weighted daily returns chained from 100. It "
        "excludes BTC, ETH and stablecoins, and approximates the non-stable altcoin market.\n"
        "- Newer tokens start at their **exchange-listing date**, not 2022.\n"
        "- Beta appears only after a full window of data is available."
    )
