#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gate.io Futures TestNet Scanner — Full Symbol List (Corrected Host)
====================================================================
- Uses official TestNet endpoint: https://api-testnet.gateapi.io/api/v4
- 53 pre-optimized configs (30 original + 23 extra)
- Single-shot execution for GitHub Actions
- Hardcoded TestNet keys (demo only — no real money)
"""

import os
import sys
import json
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from gate_api import ApiClient, Configuration, FuturesApi
from gate_api.models import FuturesOrder, FuturesPriceTriggeredOrder, FuturesInitialOrder, FuturesPriceTrigger

# ============================================================
# 🧪 TESTNET KEYS — HARDCODED (DEMO ONLY)
# ============================================================
API_KEY    = "b37ed23f61f92662e11ba8b247adfb3b"
API_SECRET = "39395a011e7d170e4b955c3060692ed5bfe3f380cd470717dbbc516b03a73fea"

# ✅ CORRECTED: Official TestNet host for Unified/Futures accounts
GATE_HOST  = "https://api-testnet.gateapi.io/api/v4"

# Safety: refuse non-TestNet hosts
if "testnet" not in GATE_HOST.lower():
    print("❌ FATAL: non-TestNet host detected. Aborting.")
    sys.exit(99)

BANNER = """
╔══════════════════════════════════════════════════════════════════════╗
║   🧪  GATE.IO FUTURES — TESTNET (DEMO)                               ║
║   ✅  VIRTUAL FUNDS ONLY — NO REAL MONEY                             ║
║   🌐  https://api-testnet.gateapi.io/api/v4                          ║
╚══════════════════════════════════════════════════════════════════════╝
"""
print(BANNER)

# ============================================================
# PARAMETERS
# ============================================================
RISK_PCT      = 0.02
ATR_MULT_SL   = 2.5
ATR_MULT_TP   = 2.5
MAX_CONTRACTS = 1_000_000
LOG_FILE      = "gate_scanner_log.jsonl"

# ============================================================
# CONFIGS — FULL LIST (30 original + 23 extra = 53 total)
# ============================================================
BEST_CONFIGS = [
    # ===== 30 ORIGINAL (from walk-forward optimization) =====
    {"symbol": "BCH/USDC",  "timeframe": "5m",  "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "AVAX/USDC", "timeframe": "5m",  "strategy": "Volume",     "params": {"vol_mult": 1.2, "ma_period": 20}},
    {"symbol": "DOT/USDC",  "timeframe": "5m",  "strategy": "Volume",     "params": {"vol_mult": 1.2, "ma_period": 50}},
    {"symbol": "GRT/USDC",  "timeframe": "5m",  "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "BCH/USDC",  "timeframe": "15m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "DOT/USDC",  "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8,  "slow": 26, "signal": 9}},
    {"symbol": "AVAX/USDC", "timeframe": "15m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "BTC/USDT",  "timeframe": "5m",  "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "AVAX/USD",  "timeframe": "15m", "strategy": "Volume",     "params": {"vol_mult": 1.2, "ma_period": 50}},
    {"symbol": "HYPE/USD",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 3, "factor": 4.236}},
    {"symbol": "CRV/USDC",  "timeframe": "5m",  "strategy": "QQE",        "params": {"rsi_period": 10, "smooth": 5, "factor": 4.236}},
    {"symbol": "LDO/USD",   "timeframe": "5m",  "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 2.5}},
    {"symbol": "CRV/USDC",  "timeframe": "5m",  "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 50}},
    {"symbol": "ETH/BTC",   "timeframe": "30m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "ETH/USDC",  "timeframe": "30m", "strategy": "Supertrend", "params": {"period": 14, "mult": 4.0}},
    {"symbol": "AVAX/USDT", "timeframe": "5m",  "strategy": "MACD",       "params": {"fast": 8,  "slow": 26, "signal": 9}},
    {"symbol": "CRV/USD",   "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8,  "slow": 26, "signal": 9}},
    {"symbol": "LINK/BTC",  "timeframe": "30m", "strategy": "Supertrend", "params": {"period": 14, "mult": 4.0}},
    {"symbol": "ETH/BTC",   "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 10, "smooth": 5, "factor": 2.5}},
    {"symbol": "ETH/USDT",  "timeframe": "30m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "CRV/USDC",  "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 12, "slow": 26, "signal": 9}},
    {"symbol": "LDO/USD",   "timeframe": "5m",  "strategy": "MACD",       "params": {"fast": 8,  "slow": 26, "signal": 9}},
    {"symbol": "ARB/USD",   "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "AVAX/USDT", "timeframe": "15m", "strategy": "MACD",       "params": {"fast": 12, "slow": 26, "signal": 9}},
    {"symbol": "FIL/USD",   "timeframe": "15m", "strategy": "Volume",     "params": {"vol_mult": 1.2, "ma_period": 20}},
    {"symbol": "FIL/USD",   "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "AAVE/USDC", "timeframe": "30m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 50}},
    {"symbol": "AAVE/USDT", "timeframe": "5m",  "strategy": "MACD",       "params": {"fast": 8,  "slow": 26, "signal": 9}},
    {"symbol": "BTC/USDC",  "timeframe": "15m", "strategy": "MACD",       "params": {"fast": 12, "slow": 26, "signal": 9}},
    {"symbol": "AVAX/USDT", "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8,  "slow": 26, "signal": 9}},

    # ===== 23 EXTRA POPULAR SYMBOLS =====
    {"symbol": "SHIB/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "PEPE/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "DOGE/USDT",  "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "ADA/USDT",   "timeframe": "30m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "NEAR/USDT",  "timeframe": "15m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "INJ/USDT",   "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "OP/USDT",    "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "SOL/USDT",   "timeframe": "15m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "WIF/USDT",   "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 10, "smooth": 3, "factor": 3.0}},
    {"symbol": "ICP/USDT",   "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "SEI/USDT",   "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "WLD/USDT",   "timeframe": "15m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "THETA/USDT", "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "STX/USDT",   "timeframe": "30m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "RUNE/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "HBAR/USDT",  "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "GALA/USDT",  "timeframe": "30m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "BONK/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "NOT/USDT",   "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "TRX/USDT",   "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "TON/USDT",   "timeframe": "15m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "SAND/USDT",  "timeframe": "30m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "MANA/USDT",  "timeframe": "30m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
]

# ============================================================
# CLIENT
# ============================================================
config = Configuration(key=API_KEY, secret=API_SECRET, host=GATE_HOST)
futures_api = FuturesApi(ApiClient(config))

# caches
_contracts_cache: dict = {}
_specs_cache:     dict = {}
_equity_cache:    dict = {}

def get_contracts(settle):
    if settle not in _contracts_cache:
        try:
            _contracts_cache[settle] = futures_api.list_futures_contracts(settle)
        except Exception as e:
            print(f"  [CACHE] contracts {settle}: {e}")
            _contracts_cache[settle] = []
    return _contracts_cache[settle]

def get_contract_specs(contract, settle):
    key = (contract, settle)
    if key in _specs_cache:
        return _specs_cache[key]
    for c in get_contracts(settle):
        if c.name == contract:
            spec = {
                "quanto_multiplier": float(c.quanto_multiplier),
                "order_size_min": int(c.order_size_min),
                "order_size_max": int(c.order_size_max),
            }
            _specs_cache[key] = spec
            return spec
    return {"quanto_multiplier": 1.0, "order_size_min": 1, "order_size_max": MAX_CONTRACTS}

# ============================================================
# INDICATORS
# ============================================================
def atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))

def supertrend(df, period=10, mult=3.0):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    a = atr(df, period).values
    hl2 = (h + l) / 2
    up_u, up_l = hl2 + mult * a, hl2 - mult * a
    n = len(c)
    fu, fl = np.full(n, np.nan), np.full(n, np.nan)
    trend = np.ones(n, dtype=int)
    for i in range(int(period), n):
        if np.isnan(up_u[i]):
            continue
        if np.isnan(fu[i - 1]):
            fu[i], fl[i] = up_u[i], up_l[i]
            trend[i] = 1 if c[i] > fl[i] else -1
            continue
        fu[i] = up_u[i] if (up_u[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = up_l[i] if (up_l[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        trend[i] = (-1 if c[i] < fl[i] else 1) if trend[i - 1] == 1 else (1 if c[i] > fu[i] else -1)
    return pd.Series(trend, index=df.index)

def qqe(df, rsi_p=14, smooth=5, factor=4.236):
    c = df["close"]
    r = rsi(c, rsi_p)
    rma = r.ewm(span=smooth, adjust=False).mean()
    rdiff = rma.diff().abs()
    atr_r = rdiff.ewm(alpha=1 / (2 * rsi_p - 1), adjust=False).mean()
    dar = atr_r * factor
    n = len(c)
    trend = np.zeros(n, dtype=int); trend[0] = 1
    lb, sb = np.full(n, np.nan), np.full(n, np.nan)
    lb[0], sb[0] = rma.iloc[0] - dar.iloc[0], rma.iloc[0] + dar.iloc[0]
    rn, dn_arr = rma.values, dar.values
    for i in range(1, n):
        nl, ns = rn[i] - dn_arr[i], rn[i] + dn_arr[i]
        lb[i] = max(lb[i - 1], nl) if (rn[i - 1] > lb[i - 1] and rn[i] > lb[i - 1]) else nl
        sb[i] = min(sb[i - 1], ns) if (rn[i - 1] < sb[i - 1] and rn[i] < sb[i - 1]) else ns
        if rn[i] > sb[i - 1]:   trend[i] = 1
        elif rn[i] < lb[i - 1]: trend[i] = -1
        else:                   trend[i] = trend[i - 1]
    return pd.Series(trend, index=df.index)

def generate_signal(df, strat, params):
    if len(df) < 2:
        return 0
    close = df["close"]
    if strat == "MACD":
        fast = close.ewm(span=params["fast"], adjust=False).mean()
        slow = close.ewm(span=params["slow"], adjust=False).mean()
        macd = fast - slow
        s = macd.ewm(span=params["signal"], adjust=False).mean()
        hist = macd - s
        st = supertrend(df, 10, 3.0)
        if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0 and st.iloc[-1] == 1:  return 1
        if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0 and st.iloc[-1] == -1: return -1
    elif strat == "QQE":
        q = qqe(df, params["rsi_period"], params["smooth"], params["factor"])
        if q.iloc[-1] == 1  and q.iloc[-2] == -1: return 1
        if q.iloc[-1] == -1 and q.iloc[-2] == 1:  return -1
    elif strat == "Volume":
        vma = df["volume"].rolling(params["ma_period"]).mean()
        if len(vma) < 2 or pd.isna(vma.iloc[-1]): return 0
        vol_ok = df["volume"].iloc[-1] > params["vol_mult"] * vma.iloc[-1]
        if vol_ok and close.iloc[-1] > close.iloc[-2]: return 1
        if vol_ok and close.iloc[-1] < close.iloc[-2]: return -1
    elif strat == "Supertrend":
        st = supertrend(df, params["period"], params["mult"])
        if st.iloc[-1] == 1  and st.iloc[-2] == -1: return 1
        if st.iloc[-1] == -1 and st.iloc[-2] == 1:  return -1
    return 0

# ============================================================
# DATA (uses corrected GATE_HOST)
# ============================================================
def fetch_gate_candles(contract, settle, timeframe, limit=200):
    url = f"{GATE_HOST}/futures/{settle}/candlesticks"
    try:
        r = requests.get(url, params={
            "contract": contract, "interval": timeframe, "limit": limit,
        }, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or len(data) < 50:
            return None
        df = pd.DataFrame(data)
        df["t"] = pd.to_datetime(df["t"].astype(int), unit="s", utc=True)
        df = df.set_index("t").sort_index()
        for col in ["o", "h", "l", "c", "v"]:
            df[col] = df[col].astype(float)
        df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
        return df
    except Exception as e:
        print(f"  ⚠️ fetch error {contract}: {e}")
        return None

# ============================================================
# CONTRACT RESOLUTION
# ============================================================
def resolve_contract(symbol):
    base, quote = symbol.split("/")
    preferred = f"{base}_{quote}"

    if quote.lower() in ("usdt", "usdc"):
        for c in get_contracts(quote.lower()):
            if c.name == preferred:
                return preferred, quote.lower()

    alt = f"{base}_USDT"
    for settle in ("usdt", "usdc"):
        for c in get_contracts(settle):
            if c.name == alt:
                return alt, settle

    return None, None

# ============================================================
# ACCOUNT / POSITION
# ============================================================
def get_equity(settle):
    try:
        return float(futures_api.list_futures_accounts(settle).total)
    except Exception:
        return 0.0

def get_position_size(contract, settle):
    try:
        for p in futures_api.list_positions(settle):
            if p.contract == contract:
                return int(p.size)
    except Exception:
        pass
    return 0

# ============================================================
# ORDERS
# ============================================================
def place_market_order(contract, size, settle):
    order = FuturesOrder(contract=contract, size=abs(size), price="0", tif="ioc", reduce_only=False)
    try:
        futures_api.create_futures_order(settle, order)
        print(f"  🧪 [DEMO] {'LONG' if size > 0 else 'SHORT'} {contract} size={abs(size)}")
        return True
    except Exception as e:
        print(f"  ❌ Market failed {contract}: {e}")
        return False

def close_position_market(contract, current_size, settle):
    order = FuturesOrder(contract=contract, size=abs(current_size), price="0", tif="ioc", reduce_only=True)
    try:
        futures_api.create_futures_order(settle, order)
        print(f"  🧪 [DEMO CLOSE] {contract} size={current_size}")
    except Exception as e:
        print(f"  ❌ Close failed {contract}: {e}")

def place_sl_tp_orders(contract, settle, side, size, sl_price, tp_price):
    sl_rule = 2 if side == "long" else 1
    tp_rule = 1 if side == "long" else 2

    for kind, price, rule in [("SL", sl_price, sl_rule), ("TP", tp_price, tp_rule)]:
        order = FuturesPriceTriggeredOrder(
            initial=FuturesInitialOrder(
                contract=contract, size=size, price="0",
                tif="ioc", reduce_only=True, close=True,
            ),
            trigger=FuturesPriceTrigger(
                strategy_type=0, price_type=0,
                price=str(price), rule=rule, expiration=86400,
            ),
            order_type=f"close-{side}-order",
        )
        try:
            futures_api.create_price_triggered_order(settle, order)
            print(f"  🧪 [DEMO {kind}] {contract} @ {price:.6f}")
        except Exception as e:
            print(f"  ⚠️ {kind} failed {contract}: {e}")

# ============================================================
# TRADING LOGIC
# ============================================================
def process_config(cfg):
    symbol = cfg["symbol"]
    tf     = cfg["timeframe"]
    strat  = cfg["strategy"]
    params = cfg["params"]

    contract, settle = resolve_contract(symbol)
    if contract is None:
        print(f"  ⚠️ {symbol}: no contract on Gate.io TestNet")
        return

    df = fetch_gate_candles(contract, settle, tf, limit=200)
    if df is None or len(df) < 50:
        return

    current_pos = get_position_size(contract, settle)
    signal = generate_signal(df, strat, params)

    if signal != 0 and current_pos == 0:
        atr_val = atr(df, 14).iloc[-1]
        price = df["close"].iloc[-1]
        if atr_val <= 0 or price <= 0:
            return

        specs = get_contract_specs(contract, settle)
        multiplier = specs["quanto_multiplier"]
        sl_dist = atr_val * ATR_MULT_SL
        tp_dist = sl_dist * ATR_MULT_TP

        equity = _equity_cache.get(settle, 0.0)
        if equity <= 0:
            print(f"  ⚠️ {symbol}: equity {settle}=0, skip")
            return

        risk_amount = equity * RISK_PCT
        size = int(risk_amount / (multiplier * sl_dist))
        size = max(specs["order_size_min"], min(size, specs["order_size_max"], MAX_CONTRACTS))
        if size <= 0:
            return

        if signal == 1:
            print(f"  📈 LONG {symbol} {tf} {strat} size={size}")
            if place_market_order(contract, size, settle):
                place_sl_tp_orders(contract, settle, "long", size,
                                   price - sl_dist, price + tp_dist)
        elif signal == -1:
            print(f"  📉 SHORT {symbol} {tf} {strat} size={size}")
            if place_market_order(contract, -size, settle):
                place_sl_tp_orders(contract, settle, "short", size,
                                   price + sl_dist, price - tp_dist)

    elif current_pos != 0:
        if (current_pos > 0 and signal == -1) or (current_pos < 0 and signal == 1):
            print(f"  🔄 Reverse {symbol}, closing")
            close_position_market(contract, current_pos, settle)

# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 70)
    print(f"🧪 ENVIRONMENT : TESTNET (DEMO) — NO REAL MONEY")
    print(f"🌐 HOST        : {GATE_HOST}")
    print(f"📊 CONFIGS     : {len(BEST_CONFIGS)}")
    print(f"🕒 TIME        : {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    try:
        acc = futures_api.list_futures_accounts("usdt")
        print(f"\n✅ Connected. USDT equity: {float(acc.total):.2f}\n")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print("   → Verify that your API key is for TestNet (Unified Account)")
        print("   → Host must be https://api-testnet.gateapi.io/api/v4")
        sys.exit(1)

    for settle in ("usdt", "usdc"):
        eq = get_equity(settle)
        if eq > 0:
            _equity_cache[settle] = eq
            print(f"💰 {settle.upper()} equity: {eq:.2f}")

    print(f"\n🚀 Processing {len(BEST_CONFIGS)} configs...\n")

    for cfg in BEST_CONFIGS:
        try:
            process_config(cfg)
        except Exception as e:
            print(f"  ❌ {cfg['symbol']} {cfg['timeframe']}: {e}")

    print(f"\n✅ Run complete — TESTNET (no real money touched)")

if __name__ == "__main__":
    main()
