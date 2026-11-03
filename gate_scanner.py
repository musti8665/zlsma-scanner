#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gate.io Futures TestNet Scanner — Final Production Build
=========================================================
- Fixes MARKET_ORDER_SIZE_TOO_LARGE (Hard Cap: 90,000 contracts)
- Fixes INCREASE_POSITION on close (size=0, close=true)
- Fixes Trigger Price scientific notation errors
- Telegram notifications enabled
"""

import os
import sys
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from gate_api import ApiClient, Configuration, FuturesApi
from gate_api.models import FuturesOrder, FuturesPriceTriggeredOrder, FuturesInitialOrder, FuturesPriceTrigger

# ============================================================
# 🧪 TESTNET KEYS
# ============================================================
API_KEY    = "b37ed23f61f92662e11ba8b247adfb3b"
API_SECRET = "39395a011e7d170e4b955c3060692ed5bfe3f380cd470717dbbc516b03a73fea"
GATE_HOST  = "https://api-testnet.gateapi.io/api/v4"

# ============================================================
# 📱 TELEGRAM CONFIG
# ============================================================
TG_TOKEN   = "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo"
TG_CHAT_ID = "254500349"

def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"  [TG] Error: {e}")

if "testnet" not in GATE_HOST.lower():
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
MAX_CONTRACTS = 90_000   # ✅ سقف مجاز برای جلوگیری از خطای صرافی (محدودیت 100,000)

# ============================================================
# CONFIGS (53 TOTAL)
# ============================================================
BEST_CONFIGS = [
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
    {"symbol": "SHIB/USDT", "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "PEPE/USDT", "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "DOGE/USDT", "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "ADA/USDT",  "timeframe": "30m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "NEAR/USDT", "timeframe": "15m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "INJ/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "OP/USDT",   "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "SOL/USDT",  "timeframe": "15m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "WIF/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 10, "smooth": 3, "factor": 3.0}},
    {"symbol": "ICP/USDT",  "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "SEI/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "WLD/USDT",  "timeframe": "15m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "THETA/USDT","timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "STX/USDT",  "timeframe": "30m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "RUNE/USDT", "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "HBAR/USDT", "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "GALA/USDT", "timeframe": "30m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "BONK/USDT", "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "NOT/USDT",  "timeframe": "15m", "strategy": "QQE",        "params": {"rsi_period": 14, "smooth": 5, "factor": 4.236}},
    {"symbol": "TRX/USDT",  "timeframe": "30m", "strategy": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "TON/USDT",  "timeframe": "15m", "strategy": "Supertrend", "params": {"period": 14, "mult": 3.0}},
    {"symbol": "SAND/USDT", "timeframe": "30m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
    {"symbol": "MANA/USDT", "timeframe": "30m", "strategy": "Volume",     "params": {"vol_mult": 1.5, "ma_period": 20}},
]

# ============================================================
# CLIENT
# ============================================================
config = Configuration(key=API_KEY, secret=API_SECRET, host=GATE_HOST)
futures_api = FuturesApi(ApiClient(config))

_contracts_cache: dict = {}
_specs_cache:     dict = {}
_equity_cache:    dict = {}

def get_contracts():
    if "usdt" not in _contracts_cache:
        try:
            _contracts_cache["usdt"] = futures_api.list_futures_contracts("usdt")
        except Exception:
            _contracts_cache["usdt"] = []
    return _contracts_cache["usdt"]

def get_contract_specs(contract):
    if contract in _specs_cache:
        return _specs_cache[contract]
    for c in get_contracts():
        if c.name == contract:
            spec = {
                "quanto_multiplier": float(c.quanto_multiplier),
                "order_size_min": int(c.order_size_min),
                "order_size_max": int(c.order_size_max),
                "order_price_round": float(c.order_price_round) if hasattr(c, "order_price_round") else 0.0001,
            }
            _specs_cache[contract] = spec
            return spec
    return {"quanto_multiplier": 1.0, "order_size_min": 1, "order_size_max": MAX_CONTRACTS, "order_price_round": 0.0001}

def round_to_tick(price, tick):
    return round(price / tick) * tick

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
        if np.isnan(up_u[i]): continue
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
    if len(df) < 2: return 0
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
# DATA & ACCOUNT
# ============================================================
def fetch_gate_candles(contract, timeframe, limit=200):
    url = f"{GATE_HOST}/futures/usdt/candlesticks"
    try:
        r = requests.get(url, params={"contract": contract, "interval": timeframe, "limit": limit}, timeout=15)
        if r.status_code != 200: return None
        data = r.json()
        if not data or len(data) < 50: return None
        df = pd.DataFrame(data)
        df["t"] = pd.to_datetime(df["t"].astype(int), unit="s", utc=True)
        df = df.set_index("t").sort_index()
        for col in ["o", "h", "l", "c", "v"]:
            df[col] = df[col].astype(float)
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        return df
    except Exception:
        return None

def resolve_contract(symbol):
    base = symbol.split("/")[0]
    alt = f"{base}_USDT"
    for c in get_contracts():
        if c.name == alt:
            return alt
    return None

def get_equity():
    try:
        return float(futures_api.list_futures_accounts("usdt").total)
    except Exception:
        return 0.0

def get_position_size(contract):
    try:
        for p in futures_api.list_positions("usdt"):
            if p.contract == contract:
                return int(p.size)
    except Exception:
        pass
    return 0

# ============================================================
# ORDERS
# ============================================================
def place_market_order(contract, size):
    order = FuturesOrder(contract=contract, size=abs(size), price="0", tif="ioc", reduce_only=False)
    try:
        futures_api.create_futures_order("usdt", order)
        print(f"  🧪 [DEMO] {'LONG' if size > 0 else 'SHORT'} {contract} size={abs(size)}")
        return True
    except Exception as e:
        print(f"  ❌ Market failed {contract}: {e}")
        send_telegram(f"❌ <b>Market failed</b>\n{contract}\n<code>{e}</code>")
        return False

def close_position_market(contract, current_size):
    order = FuturesOrder(
        contract=contract,
        size=0,                # MUST be 0 for full close
        price="0",             # market order
        tif="ioc",
        reduce_only=True,      
        close=True,            # tells exchange to close the position
    )
    try:
        futures_api.create_futures_order("usdt", order)
        print(f"  🧪 [DEMO CLOSE] {contract} (size=0, close=true)")
    except Exception as e:
        print(f"  ❌ Close failed {contract}: {e}")
        send_telegram(f"❌ <b>Close failed</b>\n{contract}\n<code>{e}</code>")

def place_sl_tp_orders(contract, side, sl_price, tp_price, entry_price, size):
    specs = get_contract_specs(contract)
    tick = specs["order_price_round"]
    sl_price = round_to_tick(sl_price, tick)
    tp_price = round_to_tick(tp_price, tick)

    price_decimals = 10
    sl_price_str = f"{sl_price:.{price_decimals}f}"
    tp_price_str = f"{tp_price:.{price_decimals}f}"

    sl_rule = 2 if side == "long" else 1
    tp_rule = 1 if side == "long" else 2

    order_type = "close-long-position" if side == "long" else "close-short-position"
    auto_size = "close_long" if side == "long" else "close_short"

    for kind, price_str, rule in [
        ("SL", sl_price_str, sl_rule),
        ("TP", tp_price_str, tp_rule),
    ]:
        order = FuturesPriceTriggeredOrder(
            initial=FuturesInitialOrder(
                contract=contract,
                size=0,
                price="0",
                tif="ioc",
                reduce_only=True,
                close=True,
                auto_size=auto_size,
            ),
            trigger=FuturesPriceTrigger(
                strategy_type=0,
                price_type=0,
                price=price_str,
                rule=rule,
                expiration=86400,
            ),
            order_type=order_type,
        )
        try:
            futures_api.create_price_triggered_order("usdt", order)
            print(f"  🧪 [DEMO {kind}] {contract} @ {price_str}")
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

    contract = resolve_contract(symbol)
    if contract is None:
        return

    df = fetch_gate_candles(contract, tf, limit=200)
    if df is None or len(df) < 50:
        return

    current_pos = get_position_size(contract)
    signal = generate_signal(df, strat, params)

    if signal != 0 and current_pos == 0:
        atr_val = atr(df, 14).iloc[-1]
        price = df["close"].iloc[-1]
        if atr_val <= 0 or price <= 0:
            return

        specs = get_contract_specs(contract)
        multiplier = specs["quanto_multiplier"]
        sl_dist = atr_val * ATR_MULT_SL
        tp_dist = sl_dist * ATR_MULT_TP

        equity = _equity_cache.get("usdt", 0.0)
        if equity <= 0:
            return

        risk_amount = equity * RISK_PCT
        raw_size = int(risk_amount / (multiplier * sl_dist))

        # ✅ اعمال سقف قرارداد و سقف مطلق بازار (90,000)
        order_size_max = specs["order_size_max"]
        size = max(specs["order_size_min"], min(raw_size, order_size_max, MAX_CONTRACTS))

        if raw_size > size:
            print(f"  ⚠️ {symbol}: requested {raw_size} capped to {size}")
            send_telegram(f"⚠️ <b>{symbol}</b>: requested {raw_size} capped to {size}")
            
        if size <= 0:
            return

        if signal == 1:
            side = "long"
            emoji = "📈"
            label = "LONG"
            print(f"  📈 LONG {symbol} {tf} {strat} size={size}")
        else:
            side = "short"
            emoji = "📉"
            label = "SHORT"
            print(f"  📉 SHORT {symbol} {tf} {strat} size={size}")

        send_telegram(
            f"{emoji} <b>{label}</b> {symbol} <code>{tf}</code>\n"
            f"🎯 Strategy: <b>{strat}</b>\n"
            f"💰 Entry: <code>{price:.6f}</code>\n"
            f"📦 Size: <code>{size}</code>\n"
            f"🛡️ SL: <code>{price - sl_dist if side == 'long' else price + sl_dist:.6f}</code>\n"
            f"🎯 TP: <code>{price + tp_dist if side == 'long' else price - tp_dist:.6f}</code>\n"
            f"🧪 <i>TESTNET — NO REAL MONEY</i>"
        )

        if side == "long":
            if place_market_order(contract, size):
                place_sl_tp_orders(contract, "long", price - sl_dist, price + tp_dist, price, size)
        else:
            if place_market_order(contract, -size):
                place_sl_tp_orders(contract, "short", price + sl_dist, price - tp_dist, price, size)

    elif current_pos != 0:
        if (current_pos > 0 and signal == -1) or (current_pos < 0 and signal == 1):
            print(f"  🔄 Reverse {symbol}, closing")
            close_position_market(contract, current_pos)
            side_label = "LONG" if current_pos > 0 else "SHORT"
            send_telegram(
                f"🚪 <b>CLOSED</b> {side_label} {symbol} <code>{tf}</code>\n"
                f"🎯 Strategy: <b>{strat}</b>\n"
                f"📦 Size: <code>{abs(current_pos)}</code>\n"
                f"🧪 <i>TESTNET</i>"
            )

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
        equity = float(acc.total)
        print(f"\n✅ Connected. USDT equity: {equity:.2f}\n")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        send_telegram(f"❌ <b>Connection failed</b>\n<code>{e}</code>")
        sys.exit(1)

    _equity_cache["usdt"] = equity

    send_telegram(
        f"🧪 <b>Gate.io TestNet Scanner — Run Started</b>\n"
        f"💰 Equity: <code>${equity:.2f} USDT</code>\n"
        f"📊 Configs: <code>{len(BEST_CONFIGS)}</code>\n"
        f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    print(f"\n🚀 Processing {len(BEST_CONFIGS)} configs...\n")

    for cfg in BEST_CONFIGS:
        try:
            process_config(cfg)
        except Exception as e:
            print(f"  ❌ {cfg['symbol']} {cfg['timeframe']}: {e}")

    print(f"\n✅ Run complete — TESTNET (no real money touched)")

if __name__ == "__main__":
    main()
