# Crypto Beta Dashboard (vs BTC)

Rolling beta of altcoin daily returns against BTC, with an interactive Streamlit dashboard.
`β = Cov(token, BTC) / Var(BTC)` over a rolling window (default 30 days ≈ 1 month).

Tokens: BTC, ETH, SOL, LDO, ENA, SQD, PENDLE, AAVE, TON, PUMP, VIRTUAL (Virtuals), SKY.

## Data

- Source: **Binance** spot daily candles (no API key); **Gate.io** fallback for SQD.
- History back to **2022-01-01** where available; newer tokens start at their listing date.
- Stored in `data/prices.csv` and **updated incrementally** — only new days are fetched,
  never re-scraped from the beginning.
- A daily **GitHub Action** (`.github/workflows/update-data.yml`, 01:30 UTC) refreshes the
  CSV and commits it, so the deployed app always reads up-to-date, persisted data.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python fetch_data.py        # build / update data/prices.csv
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)

1. Create an **empty** repo on GitHub (e.g. `beta-dashboard`), no README.
2. Push this folder:
   ```bash
   git init && git add . && git commit -m "init: crypto beta dashboard"
   git branch -M main
   git remote add origin https://github.com/<you>/beta-dashboard.git
   git push -u origin main
   ```
3. Go to <https://share.streamlit.io> → **New app** → pick the repo, branch `main`,
   main file `app.py` → **Deploy**. You get a public URL.
4. In the repo: **Settings → Actions → General → Workflow permissions → Read and write**,
   so the daily Action can commit the updated CSV. (Then run it once via the **Actions** tab →
   *Update price data* → *Run workflow* to confirm.)

The dashboard reads the committed `data/prices.csv`; the Action keeps it fresh daily, and the
in-app **🔄 Refresh** button pulls the latest days on demand.
