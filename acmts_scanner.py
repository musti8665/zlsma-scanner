#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpaca Paper Trading Scanner (Hybrid: Bybit Data + Alpaca Execution)
======================================================================
"""

import os
import sys
import json
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from numpy.lib.stride_tricks import sliding_window_view

# ============================================================
# 👇 کلیدهای Alpaca مستقیماً اینجا قرار گرفتند 👇
# ============================================================
ALPACA_KEY = "PK4UHLFQBQQNWKGATLMUPYP7CH"
ALPACA_SECRET = "4zYsHprZagiQYyVDX8q22yypaSPZHEmSs9uXB4SXf35U"

# برای تلگرام (اگر آیدی چت داری اینجا بگذار، وگرنه خالی بگذار)
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8601458987:AAEvcxx2ClOTfQIqEp9Ok6TwcvKRaMfIeqo").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip() 

ALPACA_BASE_URL = "https://paper-api.alpaca.markets/v2"
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "accept": "application/json",
    "content-type": "application/json"
}

# ارزهایی که در Alpaca موجود هستند
SYMBOLS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD", "AVAX/USD", 
    "LINK/USD", "LTC/USD", "ATOM/USD", "NEAR/USD", "UNI/USD", 
    "AAVE/USD", "FIL/USD", "SHIB/USD", "DOGE/USD"
]

ENABLE_DONCHIAN = True
ENABLE_DAILY    = True
ENABLE_CBT      = True

# پارامترهای استراتژی‌ها
DONCHIAN_PARAMS = {"entry_n": 10, "exit_n": 5, "vol_mult": 2.0, "atr_mult": 2.5, "ema_period": 100, "min_bars": 200, "tp_rr": 5.0}
DAILY_MACD_PARAMS = {"pivot_period": 8, "qqe_rsi": 20, "qqe_sf": 9, "macd_fast": 5, "macd_slow": 13, "macd_signal": 10, "sl_atr_mult": 2.5, "tp_rr": 2.0, "min_bars": 60, "vol_period": 30, "kijun_p": 20}
CBT_PVT=50; CBT_TL=2; CBT_SR_EX=3; CBT_KIJUN_LEN=36; CBT_ATR_LEN=14; CBT_ATR_BASE=250; CBT_ATR_THR=0.90
CBT_QQE_RP=14; CBT_QQE_SM=5; CBT_QQE_F=3.0; CBT_SL_MULT=2.5; CBT_TP_RR=2.5
CBT_BE_AT_R=1.0; CBT_TRAIL_R=1.5; CBT_TRAIL_ATR=1.2; CBT_MAX_BARS=40; CBT_MIN_BARS=500

MAX_TOTAL_POSITIONS = 5
RISK_PER_TRADE_PCT = 0.05 # ۵ درصد از موجودی

# ============================================================
# توابع Alpaca
# ============================================================
def get_balance():
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/account", headers=ALPACA_HEADERS, timeout=10)
        return float(r.json().get("cash", 0.0))
    except Exception as e:
        print(f"Alpaca Balance error: {e}")
        return 0.0

def get_positions():
    try:
        r = requests.get(f"{ALPACA_BASE_URL}/positions", headers=ALPACA_HEADERS, timeout=10)
        return {p["symbol"].replace("/", ""): p for p in r.json()}
    except Exception as e:
        print(f"Alpaca Positions error: {e}")
        return {}

def open_position(symbol, side, margin):
    try:
        current_price = get_current_price(symbol)
        if current_price is None: return None
        
        qty = margin / current_price
        qty = float(f"{qty:.6f}")
        
        order_side = "buy" if side == "LONG" else "sell"
        
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": order_side,
            "type": "market",
            "time_in_force": "gtc"
        }
        r = requests.post(f"{ALPACA_BASE_URL}/orders", headers=ALPACA_HEADERS, json=body, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Alpaca Open error {symbol}: {e}")
        return None

# ============================================================
# توابع دریافت دیتای بازار (از Bybit - عمومی)
# ============================================================
def get_current_price(sym):
    clean_sym = sym.replace("/", "")
    url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={clean_sym}"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get('retCode') == 0:
            return float(data['result']['list'][0]['lastPrice'])
    except:
        pass
    return None

def get_klines(sym, interval):
    clean_sym = sym.replace("/", "")
    tf_map = {"1h": "60", "4h": "240", "30m": "30"}
    bybit_interval = tf_map.get(interval, "60")
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={clean_sym}&interval={bybit_interval}&limit=500"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get('retCode') != 0: return None
        lst = data['result']['list']
        lst.reverse()
        df = pd.DataFrame(lst, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms', utc=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df.set_index('time').sort_index()
    except:
        return None

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}, timeout=15)
        return r.status_code == 200
    except: return False

# ============================================================
# توابع محاسباتی استراتژی‌ها
# ============================================================
def compute_atr(df, length=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    return pd.Series(tr).ewm(alpha=1/length, adjust=False).mean().values

def _atr_fn(h, l, c, p=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    return pd.Series(tr).ewm(alpha=1/p, adjust=False).mean().values

def _rsi_fn(c, p):
    d = np.diff(c, prepend=c[0])
    g = pd.Series(np.where(d > 0, d, 0)).ewm(alpha=1/p, adjust=False).mean().values
    lo = pd.Series(np.where(d < 0, -d, 0)).ewm(alpha=1/p, adjust=False).mean().values
    return 100 - 100 / (1 + g / np.where(lo > 0, lo, np.nan))

def _qqe_trend(c, rp=14, sm=5, factor=3.0):
    n = len(c)
    if n == 0: return np.zeros(0, dtype=int)
    r = _rsi_fn(c, rp)
    rma = pd.Series(r).ewm(span=sm, adjust=False).mean().values
    rdiff = np.abs(np.diff(r, prepend=r[0]))
    atr_r = pd.Series(rdiff).ewm(alpha=1/(2*rp-1), adjust=False).mean().values
    dar = atr_r * factor
    trend = np.zeros(n, dtype=int)
    lb = np.full(n, np.nan); sb = np.full(n, np.nan)
    lb[0] = rma[0] - dar[0]; sb[0] = rma[0] + dar[0]; trend[0] = 1
    for i in range(1, n):
        nl, ns = rma[i] - dar[i], rma[i] + dar[i]
        lb[i] = max(lb[i-1], nl) if (rma[i-1] > lb[i-1] and rma[i] > lb[i-1]) else nl
        sb[i] = min(sb[i-1], ns) if (rma[i-1] < sb[i-1] and rma[i] < sb[i-1]) else ns
        if rma[i] > sb[i-1]: trend[i] = 1
        elif rma[i] < lb[i-1]: trend[i] = -1
        else: trend[i] = trend[i-1]
    return trend

def build_tsr_short(high, low, pvt=50, tl=2, sr_except=3):
    n = len(high)
    sm = np.zeros(n, dtype=bool)
    if n < pvt * 4 + 20: return sm
    win = 2 * pvt + 1
    hw = sliding_window_view(high, win)
    ch = hw[:, pvt:pvt+1]
    is_high = (ch > hw[:, :pvt]).all(1) & (ch > hw[:, pvt+1:]).all(1)
    h_bars = np.where(is_high)[0] + pvt
    lw = sliding_window_view(low, win)
    cl = lw[:, pvt:pvt+1]
    is_low = (cl < lw[:, :pvt]).all(1) & (cl < lw[:, pvt+1:]).all(1)
    l_bars = np.where(is_low)[0] + pvt
    low_pivots  = sorted([(int(b), float(low[b]), int(b+pvt)) for b in l_bars], key=lambda p: p[2])
    high_pivots = sorted([(int(b), float(high[b]), int(b+pvt)) for b in h_bars], key=lambda p: p[2])
    for pivots, is_low_side in [(low_pivots, True), (high_pivots, False)]:
        if len(pivots) < 2: continue
        for k in range(len(pivots)):
            start = pivots[k][2]
            if start >= n: break
            end = min(pivots[k+1][2] if k+1 < len(pivots) else n, n)
            if start >= end: continue
            visible = pivots[max(0, k+1-tl):k+1]
            for i in range(len(visible) - 1):
                a_bar, a_px, _ = visible[i]
                for j in range(i+1, len(visible)):
                    b_bar, b_px, _ = visible[j]
                    if is_low_side and a_px >= b_px:   continue
                    if not is_low_side and a_px <= b_px: continue
                    if b_bar <= a_bar:                  continue
                    slope = (b_px - a_px) / (b_bar - a_bar)
                    t_range = np.arange(start, end)
                    price_t = a_px + (t_range - a_bar) * slope
                    cond = (low[start:end] <= price_t) if is_low_side else (high[start:end] >= price_t)
                    if cond.any():
                        bb = start + int(np.argmax(cond))
                        if is_low_side: sm[bb] = True
    for _, p_px, p_conf in low_pivots:
        t0 = p_conf + sr_except + 1
        if t0 >= n: continue
        cond = low[t0:] < p_px
        if cond.any(): sm[t0 + int(np.argmax(cond))] = True
    return sm

def detect_c_be_trail_signals(df):
    n = len(df)
    if n < CBT_MIN_BARS: return []
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    a = _atr_fn(h, l, c, CBT_ATR_LEN)
    atr_base = pd.Series(a).rolling(CBT_ATR_BASE).mean().values
    ratio = a / np.where(atr_base > 0, atr_base, np.nan)
    kijun = (pd.Series(h).rolling(CBT_KIJUN_LEN).max().values + pd.Series(l).rolling(CBT_KIJUN_LEN).min().values) / 2
    q_tr = _qqe_trend(c, CBT_QQE_RP, CBT_QQE_SM, CBT_QQE_F)
    tsr = build_tsr_short(h, l, CBT_PVT, CBT_TL, CBT_SR_EX)
    for offset in range(2, 5):
        i = n - offset
        if i < CBT_MIN_BARS: continue
        if pd.isna(ratio[i]) or pd.isna(kijun[i]): continue
        if not (tsr[i] and ratio[i] >= CBT_ATR_THR and c[i] < kijun[i] and q_tr[i] == -1): continue
        entry = float(c[i]); av = float(a[i])
        if av <= 0 or entry <= 0: continue
        sl = entry + CBT_SL_MULT * av
        risk = sl - entry
        tp = entry - CBT_TP_RR * risk
        sl_pct = (sl - entry) / entry * 100
        tp_pct = (entry - tp) / entry * 100
        if not (0 < sl_pct < 20): continue
        return [{"side": "SHORT", "entry": entry, "sl": sl, "tp": tp, "sl_pct": sl_pct, "tp_pct": tp_pct, "strategy": "CBT", "_tf_label": "30m"}]
    return []

def detect_donchian_signals(df, tf_label):
    P = DONCHIAN_PARAMS
    n = len(df)
    if n < P["min_bars"] or "volume" not in df.columns: return []
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    don_hi_entry = high.shift(1).rolling(P["entry_n"]).max()
    don_lo_entry = low.shift(1).rolling(P["entry_n"]).min()
    ema = close.ewm(span=P["ema_period"], adjust=False).mean()
    vol_ma = volume.rolling(20).mean()

    for offset in range(2, 15):
        i = n - offset
        if i < P["min_bars"]: continue
        if pd.isna(don_hi_entry.iloc[i]) or pd.isna(don_lo_entry.iloc[i]): continue
        if pd.isna(atr.iloc[i]) or pd.isna(ema.iloc[i]) or pd.isna(vol_ma.iloc[i]): continue
        atr_val = float(atr.iloc[i]); close_v = float(close.iloc[i]); ema_v = float(ema.iloc[i])
        vol_ok = float(volume.iloc[i]) > P["vol_mult"] * float(vol_ma.iloc[i])
        if close_v > float(don_hi_entry.iloc[i]) and vol_ok and close_v > ema_v:
            sl_price = close_v - P["atr_mult"] * atr_val
            sl_dist = close_v - sl_price
            if sl_dist > 0:
                tp_price = close_v + P["tp_rr"] * sl_dist
                sl_pct = sl_dist / close_v * 100; tp_pct = (tp_price - close_v) / close_v * 100
                if 0 < sl_pct < 25:
                    return [{"side": "LONG", "entry": close_v, "sl": sl_price, "tp": tp_price, "sl_pct": sl_pct, "tp_pct": tp_pct, "strategy": "Donchian", "_tf_label": tf_label}]
        if close_v < float(don_lo_entry.iloc[i]) and vol_ok and close_v < ema_v:
            sl_price = close_v + P["atr_mult"] * atr_val
            sl_dist = sl_price - close_v
            if sl_dist > 0:
                tp_price = close_v - P["tp_rr"] * sl_dist
                sl_pct = sl_dist / close_v * 100; tp_pct = (close_v - tp_price) / close_v * 100
                if 0 < sl_pct < 25:
                    return [{"side": "SHORT", "entry": close_v, "sl": sl_price, "tp": tp_price, "sl_pct": sl_pct, "tp_pct": tp_pct, "strategy": "Donchian", "_tf_label": tf_label}]
    return []

def detect_daily_macd_signals(df, tf_label):
    P = DAILY_MACD_PARAMS
    n = len(df)
    if n < P["min_bars"]: return []
    close, high, low = df["close"], df["high"], df["low"]
    def _e(s, nn): return s.ewm(span=nn, adjust=False).mean()
    def _r(c, nn):
        d = c.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
        return 100 - 100/(1 + g.ewm(alpha=1/nn, adjust=False).mean()/l.ewm(alpha=1/nn, adjust=False).mean().replace(0, np.nan))
    ml = _e(close, P["macd_fast"]) - _e(close, P["macd_slow"])
    sg = _e(ml, P["macd_signal"])
    ph = high.rolling(P["pivot_period"]).max(); pl = low.rolling(P["pivot_period"]).min()
    pm = (ph + pl) / 2
    rsi = _r(close, P["qqe_rsi"]); rma = _e(rsi, P["qqe_sf"])
    atr = compute_atr(df)

    for off in (2, 3):
        i = n - off
        if i < 1: continue
        cu = ml.iloc[i-1] <= sg.iloc[i-1] and ml.iloc[i] > sg.iloc[i]
        cd = ml.iloc[i-1] >= sg.iloc[i-1] and ml.iloc[i] < sg.iloc[i]
        if not cu and not cd: continue
        av = float(atr[i]); entry = float(close.iloc[i])
        if av <= 0: continue
        if cu and close.iloc[i] > pm.iloc[i] and rma.iloc[i] > 50:
            sl = entry - P["sl_atr_mult"] * av
            if sl > 0:
                tp = entry + P["tp_rr"] * (entry - sl)
                return [{"side": "LONG", "entry": entry, "sl": sl, "tp": tp, "sl_pct": (entry-sl)/entry*100, "tp_pct": (tp-entry)/entry*100, "strategy": "DailyMACD", "_tf_label": tf_label}]
        if cd and close.iloc[i] < pm.iloc[i] and rma.iloc[i] < 50:
            sl = entry + P["sl_atr_mult"] * av
            if sl > 0:
                tp = entry - P["tp_rr"] * (sl - entry)
                return [{"side": "SHORT", "entry": entry, "sl": sl, "tp": tp, "sl_pct": (sl-entry)/entry*100, "tp_pct": (entry-tp)/entry*100, "strategy": "DailyMACD", "_tf_label": tf_label}]
    return []

# ============================================================
# اجرای اصلی
# ============================================================
def run_scan():
    now = datetime.now(timezone.utc)
    print("=" * 70)
    print(f"Alpaca Paper Scan — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    
    balance_usd = get_balance()
    print(f"Alpaca Cash: ${balance_usd:,.2f}")
    if balance_usd < 10:
        print("FATAL: Balance too low.")
        return 1

    open_positions = get_positions()
    print(f"Open Positions: {len(open_positions)}")
    
    new_trades = []
    newly_opened = set()

    for sym in SYMBOLS:
        clean_sym = sym.replace("/", "")
        if clean_sym in open_positions or clean_sym in newly_opened:
            continue
        if len(open_positions) + len(new_trades) >= MAX_TOTAL_POSITIONS:
            print("Max total positions reached.")
            break

        print(f"Scanning {sym}...")
        symbol_signals = []
        
        if ENABLE_DONCHIAN:
            df_1h = get_klines(sym, "1h")
            if df_1h is not None:
                sigs = detect_donchian_signals(df_1h, "1h")
                if sigs: symbol_signals.extend(sigs)
        
        if ENABLE_DAILY:
            df_4h = get_klines(sym, "4h")
            if df_4h is not None:
                sigs = detect_daily_macd_signals(df_4h, "4h")
                if sigs: symbol_signals.extend(sigs)
                
        if ENABLE_CBT:
            df_30m = get_klines(sym, "30m")
            if df_30m is not None:
                sigs = detect_c_be_trail_signals(df_30m)
                if sigs: symbol_signals.extend(sigs)

        if not symbol_signals:
            continue

        sides = set(s["side"] for s in symbol_signals)
        if len(sides) > 1:
            continue

        PRIORITY = {"CBT": 3, "Donchian": 2, "DailyMACD": 1}
        symbol_signals.sort(key=lambda s: PRIORITY.get(s["strategy"], 0), reverse=True)
        sig = symbol_signals[0]

        margin_to_use = balance_usd * RISK_PER_TRADE_PCT
        
        print(f"  🟢 SIGNAL: {sig['strategy']} {sig['side']} {sym}")
        resp = open_position(sym, sig["side"], margin_to_use)
        
        if resp and resp.get("id"):
            new_trades.append((sym, sig, margin_to_use))
            newly_opened.add(clean_sym)
            msg = (f"🟢 <b>Alpaca Paper Trade</b>\n"
                   f"استراتژی: {sig['strategy']} | نماد: {sym}\n"
                   f"جهت: {sig['side']} | قیمت: {sig['entry']:.4f}\n"
                   f"مارجین: ${margin_to_use:.2f}")
            send_telegram(msg)

    print(f"\nResult: {len(new_trades)} new trades opened on Alpaca Paper.")
    return 0

if __name__ == "__main__":
    sys.exit(run_scan())
