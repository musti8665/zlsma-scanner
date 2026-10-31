#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpaca Master Bot - Optimized with 5m/15m/30m Best Parameters
=============================================================
"""

import os
import sys
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# ============================================================
# ALPACA CONFIG
# ============================================================
ALPACA_KEY = "PK4UHLFQBQQNWKGATLMUPYP7CH"
ALPACA_SECRET = "4zYsHprZagiQYyVDX8q22yypaSPZHEmSs9uXB4SXf35U"

ALPACA_BASE_URL = "https://paper-api.alpaca.markets/v2"
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "accept": "application/json",
    "content-type": "application/json"
}

# ============================================================
# TELEGRAM CONFIG (مستقیماً داخل کد)
# ============================================================
TG_TOKEN = "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo"
TG_CHAT = "254500349"

# ============================================================
# 🏆 TOP 8 OPTIMIZED SHORT-TIMEFRAME STRATEGIES
# ============================================================
MASTER_STRATEGIES = [
    {"symbol": "AVAX/USD", "tf": "15m", "type": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 9}},
    {"symbol": "SKY/USD",  "tf": "30m", "type": "QQE",        "params": {"rsi_period": 14, "smooth": 3, "factor": 2.5}},
    {"symbol": "SKY/USD",  "tf": "30m", "type": "Supertrend", "params": {"period": 14, "mult": 2.0}},
    {"symbol": "POL/USD",  "tf": "5m",  "type": "QQE",        "params": {"rsi_period": 14, "smooth": 7, "factor": 4.236}},
    {"symbol": "CRV/USD",  "tf": "30m", "type": "MACD",       "params": {"fast": 8, "slow": 26, "signal": 7}},
    {"symbol": "WIF/USD",  "tf": "15m", "type": "QQE",        "params": {"rsi_period": 10, "smooth": 3, "factor": 3.0}},
    {"symbol": "FIL/USD",  "tf": "15m", "type": "QQE",        "params": {"rsi_period": 10, "smooth": 3, "factor": 2.5}},
    {"symbol": "LINK/USD", "tf": "30m", "type": "Supertrend", "params": {"period": 14, "mult": 4.0}},
]

RISK_PER_TRADE_USD = 1000  # مارجین هر معامله در Alpaca

# ============================================================
# Alpaca Functions
# ============================================================
def get_positions():
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS, timeout=10)
        return {p["symbol"].replace("/", ""): p for p in r.json()}
    except Exception as e:
        print(f"Error getting positions: {e}")
        return {}

def open_position(symbol, side, margin):
    try:
        price = get_current_price(symbol)
        if price is None: return None
        qty = float(f"{margin / price:.6f}")
        order_side = "buy" if side == "LONG" else "sell"
        
        body = {"symbol": symbol, "qty": str(qty), "side": order_side, "type": "market", "time_in_force": "gtc"}
        r = requests.post(f"{ALPACA_BASE_URL}/orders", headers=ALPACA_HEADERS, json=body, timeout=10)
        return r.json() if r.status_code in (200, 201) else None
    except Exception as e:
        print(f"Error opening position: {e}")
        return None

def close_position(symbol, side, qty):
    try:
        order_side = "sell" if side == "LONG" else "buy"
        body = {"symbol": symbol, "qty": str(qty), "side": order_side, "type": "market", "time_in_force": "gtc"}
        requests.post(f"{ALPACA_BASE_URL}/orders", headers=ALPACA_HEADERS, json=body, timeout=10)
        return True
    except:
        return False

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT: 
        print("  [!] Telegram Error: Token or Chat ID is missing.")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=15)
        if r.status_code == 200:
            print("  [✓] Telegram message sent successfully.")
            return True
        else:
            print(f"  [!] Telegram Error: HTTP {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print(f"  [!] Telegram Exception: {e}")
        return False

# ============================================================
# Data Fetching (Bybit) - پشتیبانی از تایم‌فریم‌های جدید
# ============================================================
def get_bybit_symbol(sym):
    return f"{sym.split('/')[0]}USDT"

def get_current_price(sym):
    url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={get_bybit_symbol(sym)}"
    try:
        r = requests.get(url, timeout=10).json()
        if r.get('retCode') == 0: return float(r['result']['list'][0]['lastPrice'])
    except: pass
    return None

def get_klines(sym, interval):
    # تبدیل فرمت‌های 5m, 15m, 30m به فرمت بای‌بیت
    tf_map = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240"}
    bybit_interval = tf_map.get(interval, "15")
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={get_bybit_symbol(sym)}&interval={bybit_interval}&limit=500"
    try:
        r = requests.get(url, timeout=10).json()
        if r.get('retCode') != 0: return None
        lst = r['result']['list']
        lst.reverse()
        df = pd.DataFrame(lst, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except:
        return None

# ============================================================
# Indicators & Signals
# ============================================================
def calc_supertrend(df, period=10, mult=3.0):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1/period, adjust=False).mean().values
    hl2 = (h + l) / 2
    up_u, up_l = hl2 + mult*a, hl2 - mult*a
    n = len(c)
    fu, fl, trend = np.full(n, np.nan), np.full(n, np.nan), np.ones(n, dtype=int)
    for i in range(int(period), n):
        if np.isnan(up_u.iloc[i]): continue
        if np.isnan(fu[i-1]):
            fu[i], fl[i] = up_u.iloc[i], up_l.iloc[i]
            trend[i] = 1 if c.iloc[i] > fl[i] else -1
            continue
        fu[i] = up_u.iloc[i] if (up_u.iloc[i] < fu[i-1] or c.iloc[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = up_l.iloc[i] if (up_l.iloc[i] > fl[i-1] or c.iloc[i-1] < fl[i-1]) else fl[i-1]
        trend[i] = (-1 if c.iloc[i] < fl[i] else 1) if trend[i-1] == 1 else (1 if c.iloc[i] > fu[i] else -1)
    return pd.Series(trend, index=df.index)

def calc_qqe(df, rsi_p, smooth, factor):
    c = df['close']
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1/rsi_p, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1/rsi_p, adjust=False).mean()
    rs = 100 - 100/(1 + up/dn.replace(0, np.nan))
    rma = rs.ewm(span=smooth, adjust=False).mean()
    rdiff = rma.diff().abs()
    wilder = 2*rsi_p - 1
    atr_r = rdiff.ewm(alpha=1/wilder, adjust=False).mean()
    dar = atr_r * factor
    n = len(c)
    trend = np.zeros(n, dtype=int); trend[0] = 1
    lb, sb = np.full(n, np.nan), np.full(n, np.nan)
    lb[0], sb[0] = rma.iloc[0] - dar.iloc[0], rma.iloc[0] + dar.iloc[0]
    rn, dn_arr = rma.values, dar.values
    for i in range(1, n):
        nl, ns = rn[i] - dn_arr[i], rn[i] + dn_arr[i]
        lb[i] = max(lb[i-1], nl) if (rn[i-1] > lb[i-1] and rn[i] > lb[i-1]) else nl
        sb[i] = min(sb[i-1], ns) if (rn[i-1] < sb[i-1] and rn[i] < sb[i-1]) else ns
        if rn[i] > sb[i-1]: trend[i] = 1
        elif rn[i] < lb[i-1]: trend[i] = -1
        else: trend[i] = trend[i-1]
    return pd.Series(trend, index=df.index)

def check_signal(df, strat_type, params, current_side):
    close = df['close']
    
    if strat_type == "MACD":
        macd = close.ewm(span=params["fast"]).mean() - close.ewm(span=params["slow"]).mean()
        sig = macd.ewm(span=params["signal"]).mean()
        hist = macd - sig
        
        if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0: return "LONG"
        if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0: return "SHORT"
        if current_side == "LONG" and hist.iloc[-1] < 0: return "CLOSE"
        if current_side == "SHORT" and hist.iloc[-1] > 0: return "CLOSE"

    elif strat_type == "QQE":
        q = calc_qqe(df, params["rsi_period"], params["smooth"], params["factor"])
        if q.iloc[-1] == 1 and q.iloc[-2] == -1: return "LONG"
        if q.iloc[-1] == -1 and q.iloc[-2] == 1: return "SHORT"
        if current_side == "LONG" and q.iloc[-1] == -1: return "CLOSE"
        if current_side == "SHORT" and q.iloc[-1] == 1: return "CLOSE"

    elif strat_type == "Supertrend":
        st = calc_supertrend(df, params["period"], params["mult"])
        if st.iloc[-1] == 1 and st.iloc[-2] == -1: return "LONG"
        if st.iloc[-1] == -1 and st.iloc[-2] == 1: return "SHORT"
        if current_side == "LONG" and st.iloc[-1] == -1: return "CLOSE"
        if current_side == "SHORT" and st.iloc[-1] == 1: return "CLOSE"
        
    return None

# ============================================================
# Main Execution
# ============================================================
def run_scan():
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"Alpaca 5m/15m/30m Optimized Bot — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    
    open_positions = get_positions()
    print(f"Open Positions: {len(open_positions)}")
    
    for strat in MASTER_STRATEGIES:
        sym = strat["symbol"]
        tf = strat["tf"]
        stype = strat["type"]
        params = strat["params"]
        
        clean_sym = sym.replace("/", "")
        df = get_klines(sym, tf)
        if df is None or len(df) < 50: continue
        
        current_pos = open_positions.get(clean_sym)
        current_side = ""
        if current_pos:
            current_side = "LONG" if current_pos['side'].lower() == "buy" or current_pos.get('qty', 0) > 0 else "SHORT"
            
        action = check_signal(df, stype, params, current_side)
        
        if action == "CLOSE" and current_pos:
            print(f"  🔴 CLOSING {sym} {current_side} ({stype} Exit)")
            close_position(sym, current_side, current_pos['qty'])
            send_telegram(f"🔴 <b>Position Closed</b>\n{sym} {current_side}\nStrategy: {stype} ({tf})")
            
        elif action in ("LONG", "SHORT") and not current_pos:
            print(f"  🟢 OPENING {sym} {action} ({stype} {tf})")
            resp = open_position(sym, action, RISK_PER_TRADE_USD)
            if resp:
                msg = (f"🟢 <b>Position Opened</b>\n"
                       f"Symbol: <b>{sym}</b>\n"
                       f"Side: <b>{action}</b>\n"
                       f"Strategy: {stype} ({tf})\n"
                       f"Margin: ${RISK_PER_TRADE_USD}")
                send_telegram(msg)
            else:
                print(f"  [!] Failed to open position on Alpaca for {sym}")

    print("Scan complete.")
    return 0

if __name__ == "__main__":
    sys.exit(run_scan())
